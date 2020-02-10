#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
    gns3_proxy_manage_images

    Management of GNS3 images across multiple backend nodes, e.g., behind a GNS3 proxy

    :copyright: (c) 2020 by Sebastian Rieger.
    :license: BSD, see LICENSE for more details.
"""

import argparse
import configparser
import json
import logging
import re
import sys
import os
import time
from ipaddress import ip_address
from packaging import version

import requests

VERSION = (0, 3)
__version__ = '.'.join(map(str, VERSION[0:2]))
__description__ = 'GNS3 Proxy Manage Images'
__author__ = 'Sebastian Rieger'
__author_email__ = 'sebastian@riegers.de'
__homepage__ = 'https://github.com/srieger1/gns3-proxy'
__download_url__ = '%s/archive/develop.zip' % __homepage__
__license__ = 'BSD'

logger = logging.getLogger(__name__)

PY3 = sys.version_info[0] == 3

if PY3:  # pragma: no cover
    text_type = str
    binary_type = bytes

# else:  # pragma: no cover
#    text_type = unicode
#    binary_type = str
#    import urlparse


# Default arguments
DEFAULT_CONFIG_FILE = 'gns3_proxy_config.ini'
DEFAULT_LOG_LEVEL = 'INFO'
DEFAULT_DELETE_ACTION = False
DEFAULT_SHOW_ACTION = False
DEFAULT_FORCE = False
DEFAULT_INCLUDE_BUILTIN = False


class ProxyError(Exception):
    pass


def parse_args(args):
    parser = argparse.ArgumentParser(
        description='gns3_proxy_manage_images.py v%s Manage templates on GNS3 proxy backends.' % __version__,
        epilog='gns3_proxy not working? Report at: %s/issues/new' % __homepage__
    )
    # Argument names are ordered alphabetically.
    parser.add_argument('--config-file', type=str, default=DEFAULT_CONFIG_FILE,
                        help='Location of the gns3_proxy config file. Default: gns3_proxy_config.ini.')
    parser.add_argument('--log-level', type=str, default=DEFAULT_LOG_LEVEL,
                        help='Valid options: DEBUG, INFO (default), WARNING, ERROR, CRITICAL. '
                             'Both upper and lowercase values are allowed.'
                             'You may also simply use the leading character e.g. --log-level d')

    parser.add_argument('--force', action='store_true', default=DEFAULT_FORCE,
                        help='Force action without further prompt. E.g., delete templates without further '
                             'verification.')

    parser.add_argument('--template-name', type=str, required=True,
                        help='Name of the template to be managed.'
                             'Can be specified as a regular expression to match multiple templates.')

    parser.add_argument('--include-builtin', action='store_true', default=DEFAULT_INCLUDE_BUILTIN,
                        help='Include builtin templates.')

    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument('--export-to-dir', type=str,
                              help='Export template to directory.')
    action_group.add_argument('--import-from-file', type=str,
                              help='Import template from file.')
    action_group.add_argument('--delete', action='store_true', default=DEFAULT_DELETE_ACTION,
                              help='Delete templates.')
    action_group.add_argument('--show', action='store_true', default=DEFAULT_SHOW_ACTION,
                              help='Show templates and their status.')

    parser.add_argument('--target-server', type=str, required=True,
                        help='Target(s) to copy project to. Name of a servers/backends defined in the config file. '
                             'Can be specified as a regular expression to match multiple target servers.')

    return parser.parse_args(args)


def main():
    # parse arguments
    args = parse_args(sys.argv[1:])

    # parse config file gns3_proxy_config.ini
    config = configparser.ConfigParser()
    config.read_file(open(args.config_file))
    config.read(args.config_file)

    logging.basicConfig(level=getattr(logging, args.log_level),
                        format='%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s')

    # get backend_user
    #
    # description: Username to use to access backend GNS3 server
    # default: admin
    if config.get('proxy', 'backend_user'):
        backend_user = config.get('proxy', 'backend_user')
    else:
        backend_user = "admin"

    # get backend_password
    #
    # description: Password to use to access backend GNS3 server
    # default: password
    if config.get('proxy', 'backend_password'):
        backend_password = config.get('proxy', 'backend_password')
    else:
        backend_password = "password"

    # get backend_port
    #
    # description: TCP port to use to access backend GNS3 server
    # default: 3080
    if config.get('proxy', 'backend_port'):
        backend_port = config.getint('proxy', 'backend_port')
    else:
        backend_port = 3080

    # read servers from config
    if config.items('servers'):
        config_servers = dict()
        server_items = config.items('servers')
        for server, value in server_items:
            try:
                ip_address(value)
            except ValueError:
                logger.fatal("server config %s is not a valid IP address (e.g., 1.2.3.4)" % value)
                raise ProxyError()
            config_servers[server] = value
    else:
        config_servers = None

    logger.debug("Config backend_user: %s" % backend_user)
    logger.debug("Config backend_password: %s" % backend_password)
    logger.debug("Config backend_port: %s" % backend_port)

    logger.debug("Config servers: %s" % config_servers)

    try:
        username = backend_user
        password = backend_password

        # Try to find match for target server in config
        if len(config_servers) > 0:
            for server in config_servers:
                if re.fullmatch(args.target_server, server):
                    logger.debug("Target server found: %s (%s) using provided match: %s" % (server,
                                                                                            config_servers[server],
                                                                                            args.target_server))
                    # build target server API URL
                    base_dst_api_url = "http://" + config_servers[server] + ":" + str(backend_port) + "/v2"

                    # check version of target server, template format changed from GNS3 2.1 to 2.2

                    url = base_dst_api_url + '/version'
                    r = requests.get(url, auth=(username, password))
                    if r.status_code == 200:
                        version_results = json.loads(r.text)
                        server_version = version_results['version']
                        if version.parse(server_version) < version.parse("2.2.0"):
                            # logger.fatal("Target server must use GNS3 >= 2.2. Template format has changed. See GNS3 "
                            #              "2.2 installation documentation, for steps to migrate GNS3 server from 2.1 "
                            #              "to 2.2.")
                            # raise ProxyError()

                            print("Target server is running GNS3 <2.2 (%s) using old appliance template API" %
                                  server_version)
                            new_template_api = False
                        else:
                            print("Target server is running GNS3 >=2.2 (%s) using new template API" %
                                  server_version)
                            new_template_api = True
                    else:
                        logger.fatal("Could not connect to target server. Could not determine its version.")
                        raise ProxyError()

                    if args.show:
                        print("#### Showing template %s on server: %s" % (args.template_name, server))

                        logger.debug("Getting templates...")
                        if new_template_api:
                            url = base_dst_api_url + '/templates'
                        else:
                            url = base_dst_api_url + '/appliances'
                        r = requests.get(url, auth=(username, password))
                        if r.status_code == 200:
                            template_results = json.loads(r.text)
                            for template in template_results:

                                # skip builtin templates like Cloud, NAT, VPCS, Ethernet switch, Ethernet hub,
                                # Frame Relay switch, ATM switch
                                if template['builtin'] and args.include_builtin is False:
                                    logger.debug("#### Skipping builtin template: %s" % template['name'])
                                    continue

                                if re.fullmatch(args.template_name, template['name']):
                                    print("#### Server: %s, Template: %s"
                                          % (server, template))
                        else:
                            logger.fatal("Could not get status of templates from.")
                            raise ProxyError()

                    if args.delete:
                        print("#### Deleting template %s on server: %s" % (args.template_name, server))

                        logger.debug("Getting templates...")
                        if new_template_api:
                            url = base_dst_api_url + '/templates'
                        else:
                            logger.fatal("Deletion of templates is not supported on target servers using GNS3"
                                         " <2.2 (old template API).")
                            raise ProxyError()

                        r = requests.get(url, auth=(username, password))
                        if r.status_code == 200:
                            template_results = json.loads(r.text)
                            for template in template_results:

                                # skip builtin templates like Cloud, NAT, VPCS, Ethernet switch, Ethernet hub,
                                # Frame Relay switch, ATM switch
                                if template['builtin'] and args.include_builtin is False:
                                    logger.debug("#### Skipping builtin template: %s" % template['name'])
                                    continue

                                if re.fullmatch(args.template_name, template['name']):
                                    if args.force:
                                        logger.debug("Deleting template %s on server: %s"
                                                     % (template['name'], config_servers[server]))

                                        if new_template_api:
                                            r = requests.delete(
                                                base_dst_api_url + '/templates/' + template['template_id'],
                                                auth=(username, password))
                                        else:
                                            r = requests.delete(
                                                base_dst_api_url + '/appliances/' + template['appliance_id'],
                                                auth=(username, password))

                                        if not r.status_code == 204:
                                            if r.status_code == 404:
                                                logger.debug("Template did not exist before, not deleted")
                                            else:
                                                logger.fatal("unable to delete template")
                                                raise ProxyError()
                                        else:
                                            print("#### Deleted template %s on server: %s"
                                                  % (template['name'], config_servers[server]))
                                    else:
                                        print("     WARNING: Template %s to delete found on server: %s, use --force"
                                              " to really remove it." % (template['name'], config_servers[server]))
                        else:
                            logger.fatal("Could not get status of templates from server %s." % config_servers[server])
                            raise ProxyError()

                    if args.export_to_dir:
                        print("#### Exporting template %s on server: %s" % (args.template_name, server))

                        logger.debug("Getting templates from target server...")
                        if new_template_api:
                            url = base_dst_api_url + '/templates'
                        else:
                            url = base_dst_api_url + '/appliances'
                        r = requests.get(url, auth=(username, password))
                        if r.status_code == 200:
                            template_results = json.loads(r.text)
                            for template in template_results:

                                # skip builtin templates like Cloud, NAT, VPCS, Ethernet switch, Ethernet hub,
                                # Frame Relay switch, ATM switch
                                if template['builtin'] and args.include_builtin is False:
                                    logger.debug("#### Skipping builtin template: %s" % template['name'])
                                    continue

                                if re.fullmatch(args.template_name, template['name']):
                                    logger.debug("Found template: %s on server %s"
                                                 % (template['name'], server))

                                    if new_template_api:
                                        filename = str(server) + "_" + template['template_type'] + "_" \
                                                   + template['name'] + "_" + template['template_id'] + "_" \
                                                   + time.strftime("%Y%m%d-%H%M%S") + ".gns3a"
                                    else:
                                        filename = "MIGRATED_" + str(server) + "_" + template['node_type'] + "_" \
                                                   + template['name'] + "_" + template['appliance_id'] + "_" \
                                                   + time.strftime("%Y%m%d-%H%M%S") + ".gns3a"

                                        # old <2.2 GNS3 API did not include config of the template in appliance
                                        # definition needs to be extracted from settings
                                        url = base_dst_api_url + '/settings'
                                        r = requests.get(url, auth=(username, password))
                                        if r.status_code == 200:
                                            settings_results = json.loads(r.text)
                                            if template['node_type'] == "cloud":
                                                for cloud_node in settings_results['Builtin']['cloud_nodes']:
                                                    if cloud_node['name'] == template['name']:
                                                        template.update(cloud_node)

                                            elif template['node_type'] == "ethernet_hub":
                                                for ethernet_hub_node in settings_results['Builtin']['ethernet_hubs']:
                                                    if ethernet_hub_node['name'] == template['name']:
                                                        template.update(ethernet_hub_node)

                                            elif template['node_type'] == "ethernet_switch":
                                                for ethernet_switch_node in \
                                                        settings_results['Builtin']['ethernet_switches']:
                                                    if ethernet_switch_node['name'] == template['name']:
                                                        template.update(ethernet_switch_node)

                                            elif template['node_type'] == "docker":
                                                for container_node in settings_results['Docker']['containers']:
                                                    if container_node['name'] == template['name']:
                                                        template.update(container_node)

                                            elif template['node_type'] == "dynamips":
                                                for router_node in settings_results['Dynamips']['routers']:
                                                    if router_node['name'] == template['name']:
                                                        # 'chassis' and 'iomem' not supported in GNS3 >=2.2
                                                        router_node.pop('chassis', None)
                                                        router_node.pop('iomem', None)
                                                        template.update(router_node)

                                            elif template['node_type'] == "iou":
                                                for iou_node in settings_results['IOU']['devices']:
                                                    if iou_node['name'] == template['name']:
                                                        template.update(iou_node)

                                            elif template['node_type'] == "qemu":
                                                for vm_node in settings_results['Qemu']['vms']:
                                                    if vm_node['name'] == template['name']:
                                                        # 'acpi_shutdown' not supported in GNS3 >=2.2
                                                        vm_node.pop('acpi_shutdown', None)
                                                        template.update(vm_node)

                                            elif template['node_type'] == "vmware":
                                                for vmware_node in settings_results['VMware']['vms']:
                                                    if vmware_node['name'] == template['name']:
                                                        template.update(vmware_node)

                                            elif template['node_type'] == "vpcs":
                                                for vpc_node in settings_results['VPCS']['nodes']:
                                                    if vpc_node['name'] == template['name']:
                                                        template.update(vpc_node)

                                            elif template['node_type'] == "virtualbox":
                                                for virtualbox_node in settings_results['VirtualBox']['vms']:
                                                    if virtualbox_node['name'] == template['name']:
                                                        template.update(virtualbox_node)

                                            else:
                                                logger.fatal(
                                                    "Template type %s of template %s not supported. Cannot be "
                                                    "converted."
                                                    % (template['node_type'], template['name']))
                                                raise ProxyError()

                                        else:
                                            logger.fatal(
                                                "Could not get settings to export template to new format for %s."
                                                % template['name'])
                                            raise ProxyError()

                                        # old <2.2 GNS3 API used appliance_id and node_type, needs to be
                                        # converted to be able to import template to 2.2

                                        # 'appliance_id' is now 'template_id' in GNS3 2.2
                                        # 'node_type' is now 'template_type' in GNS3 2.2
                                        template['template_id'] = template.pop('appliance_id')
                                        template['template_type'] = template.pop('node_type')

                                        # platform could be null is old GNS3 2.1 templates, GNS3 2.2 only allows the
                                        # following:
                                        # None is not one of [\'aarch64\', \'alpha\', \'arm\', \'cris\', \'i386\',
                                        # \'lm32\', \'m68k\', \'microblaze\', \'microblazeel\', \'mips\', \'mips64\',
                                        # \'mips64el\', \'mipsel\', \'moxie\', \'or32\', \'ppc\', \'ppc64\', \'ppcemb\',
                                        # \'s390x\', \'sh4\', \'sh4eb\', \'sparc\', \'sparc64\', \'tricore\',
                                        # \'unicore32\', \'x86_64\', \'xtensa\', \'xtensaeb\', \'\']"
                                        if 'platform' in template:
                                            if template['platform'] is None:
                                                template.pop('platform')

                                    with open(os.path.join(args.export_to_dir, filename), 'w',
                                              encoding="utf8") as outfile:
                                        json.dump(template, outfile, sort_keys=True, indent=4)

                                    print("#### Exported template %s from server: %s to file: "
                                          % (template['name'], config_servers[server]),
                                          os.path.join(args.export_to_dir, filename))

                        else:
                            logger.fatal("Could not get status of templates from server %s." % config_servers[server])
                            raise ProxyError()

                    if args.import_from_file:
                        print("#### Importing template %s on server: %s" % (args.template_name, server))

                        logger.debug("Checking if target template exists...")
                        if new_template_api:
                            url = base_dst_api_url + '/templates'
                        else:
                            logger.fatal("Import of templates is not supported on target servers using GNS3"
                                         " <2.2 (old template API).")
                            raise ProxyError()
                        r = requests.get(url, auth=(username, password))
                        if r.status_code == 200:
                            template_exists = False
                            template_results = json.loads(r.text)
                            template = None
                            for template in template_results:
                                if re.fullmatch(args.template_name, template['name']):
                                    logger.debug("Template: %s already exists on server %s"
                                                 % (template, server))
                                    if template_exists:
                                        logger.fatal(
                                            "Multiple templates matched %s on server %s. "
                                            "Import can only be used for single template." % (
                                                args.template_name, config_servers[
                                                    server]))
                                        raise ProxyError()
                                    else:
                                        template_exists = True
                            if template_exists:
                                if args.force:
                                    print("#### Forcing deletion of template %s on server: %s" % (
                                        args.template_name, server))

                                    logger.debug("Deleting template %s on server: %s"
                                                 % (template['name'], config_servers[server]))
                                    r = requests.delete(
                                        base_dst_api_url + '/templates/' + template['template_id'],
                                        auth=(username, password))
                                    if not r.status_code == 204:
                                        if r.status_code == 404:
                                            logger.debug("Template did not exist before, not deleted")
                                        else:
                                            logger.fatal("unable to delete template")
                                            raise ProxyError()
                                    else:
                                        print("#### Deleted template %s on server: %s"
                                              % (template['name'], config_servers[server]))
                                else:
                                    logger.fatal(
                                        "Template: %s already exists on server %s. Use --force to overwrite it"
                                        " during import."
                                        % (template['name'], server))
                                    raise ProxyError()

                            logger.debug("Importing template")
                            # import template
                            url = base_dst_api_url + '/templates'
                            with open(args.import_from_file, 'rb') as payload:
                                headers = {'content-type': 'application/json'}
                                r = requests.post(url, auth=(username, password),
                                                  data=payload, verify=False, headers=headers)
                            if not r.status_code == 201:
                                if r.status_code == 403:
                                    logger.fatal("Forbidden to import template on target server.")
                                    raise ProxyError()
                                else:
                                    logger.fatal(
                                        "Unable to import template on target server. Response: %s " % r.content)
                                    raise ProxyError()
                            else:
                                print("#### Template %s imported from file: %s on server: %s"
                                      % (template['name'], args.import_from_file, server))

                        else:
                            logger.fatal("Could not get status of templates from server %s." % config_servers[server])
                            raise ProxyError()

        print("Done.")

    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
