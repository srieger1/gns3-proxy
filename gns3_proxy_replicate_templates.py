#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
    gns3_proxy_manage_images

    Replication of GNS3 templates across multiple backend nodes, e.g., behind a GNS3 proxy

    :copyright: (c) 2020 by Sebastian Rieger.
    :license: BSD, see LICENSE for more details.
"""

import argparse
import configparser
import json
import logging
import re
import sys
from ipaddress import ip_address
from packaging import version

import requests

VERSION = (0, 4)
__version__ = '.'.join(map(str, VERSION[0:2]))
__description__ = 'GNS3 Proxy Replicate Templates'
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
DEFAULT_FORCE = False


class ProxyError(Exception):
    pass


def parse_args(args):
    parser = argparse.ArgumentParser(
        description='gns3_proxy_replicate_templates.py v%s Replicates templates on GNS3 proxy backends.' % __version__,
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
                        help='Name of the image to be replicated.'
                             'Can be specified as a regular expression to match multiple templates.')

    parser.add_argument('--source-server', type=str, required=True,
                        help='Source server to copy templates from. A name of a server/backend defined in the '
                             'config file.')
    parser.add_argument('--target-server', type=str, required=True,
                        help='Target(s) to copy templates to. Name of a servers/backends defined in the config file. '
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

        # get source server IP
        if args.source_server in config_servers:
            src_server = config_servers[args.source_server]
            logger.debug("Source server will be %s:%s" % (
                src_server, backend_port))
        else:
            logger.fatal("Source server not found in config.")
            raise ProxyError()

        base_src_api_url = "http://" + src_server + ":" + str(backend_port) + "/v2"

        url = base_src_api_url + '/version'
        r = requests.get(url, auth=(username, password))
        if r.status_code == 200:
            version_results = json.loads(r.text)
            server_version = version_results['version']
            if version.parse(server_version) < version.parse("2.2.0"):
                # logger.fatal("Target server must use GNS3 >= 2.2. Template format has changed. See GNS3 "
                #              "2.2 installation documentation, for steps to migrate GNS3 server from 2.1 "
                #              "to 2.2.")
                # raise ProxyError()

                print("Source server is running GNS3 <2.2 (%s) using old appliance template API" %
                      server_version)
                src_new_template_api = False
            else:
                print("Source server is running GNS3 >=2.2 (%s) using new template API" %
                      server_version)
                src_new_template_api = True
        else:
            logger.fatal("Could not connect to target server. Could not determine its version.")
            raise ProxyError()

        logger.debug("Searching source templates")
        templates = list()
        if src_new_template_api:
            url = base_src_api_url + '/templates'
        else:
            url = base_src_api_url + '/appliances'

        r = requests.get(url, auth=(username, password))
        if not r.status_code == 200:
            logger.fatal("Could not list templates.")
            raise ProxyError()
        else:
            template_results = json.loads(r.text)
            for template in template_results:
                if re.fullmatch(args.template_name, template['name']):
                    logger.debug('matched template: %s' % template['name'])

                    # skip builtin templates like Cloud, NAT, VPCS, Ethernet switch, Ethernet hub, Frame Relay switch,
                    # ATM switch
                    if template['builtin']:
                        print("#### Skipping builtin template: %s" % template['name'])
                        continue

                    if not src_new_template_api:
                        # old <2.2 GNS3 API did not include config of the template in appliance
                        # definition needs to be extracted from settings
                        url = base_src_api_url + '/settings'
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

                        # platform could be null is old GNS3 2.1 templates, GNS3 2.2 only allows the following:
                        # None is not one of [\'aarch64\', \'alpha\', \'arm\', \'cris\', \'i386\', \'lm32\', \'m68k\',
                        # \'microblaze\', \'microblazeel\', \'mips\', \'mips64\', \'mips64el\', \'mipsel\', \'moxie\',
                        # \'or32\', \'ppc\', \'ppc64\', \'ppcemb\', \'s390x\', \'sh4\', \'sh4eb\', \'sparc\',
                        # \'sparc64\', \'tricore\', \'unicore32\', \'x86_64\', \'xtensa\', \'xtensaeb\', \'\']"
                        if 'platform' in template:
                            if template['platform'] is None:
                                template.pop('platform')

                    templates.append(template)

        if len(templates) == 0:
            logger.fatal("Specified template not found.")
            raise ProxyError()

        for template in templates:
            template_name = template['name']
            template_id = template['template_id']

            print("#### Replicating template: %s" % template_name)

            # target handling

            # Try to find match for target server in config
            target_server_addresses = list()
            if len(config_servers) > 0:
                for key in config_servers:
                    if re.fullmatch(args.target_server, key):
                        logger.debug("Target server found: %s (%s) using provided match: %s" % (key,
                                                                                                config_servers[key],
                                                                                                args.target_server))
                        if key == args.source_server:
                            logger.debug("Target server %s is the same as the source server %s . Filtered out."
                                         % (key, args.source_server))
                        else:
                            target_server_addresses.append(config_servers[key])
            else:
                logger.fatal("No servers defined in config. Could not select target server.")
                raise ProxyError()

            if len(target_server_addresses) == 0:
                logger.fatal("No target servers found using match: %s. Could not select target server."
                             % args.target_server)
                raise ProxyError()

            for target_server_address in target_server_addresses:
                logger.debug("    #### Replicating template: %s to server: %s" % (template_name, target_server_address))
                base_dst_api_url = "http://" + target_server_address + ":" + str(backend_port) + "/v2"
                url = base_dst_api_url + '/version'
                r = requests.get(url, auth=(username, password))
                if r.status_code == 200:
                    version_results = json.loads(r.text)
                    server_version = version_results['version']
                    if version.parse(server_version) < version.parse("2.2.0"):
                        logger.fatal("Target server must use GNS3 >= 2.2. Template format has changed. You can use"
                                     " gns3_proxy_manage_templates.py to export templates from 2.1, automatically"
                                     " convert them to GNS3 2.2 format and import them to a new GNS3 2.2 server.")
                        raise ProxyError()
                else:
                    logger.fatal("Could not connect to source server. Could not determine its version.")
                    raise ProxyError()

                logger.debug("Checking if target template name exists...")
                url = base_dst_api_url + '/templates'
                r = requests.get(url, auth=(username, password))
                if r.status_code == 200:
                    target_template_name_exists = False
                    target_template_name_to_delete = None
                    target_template_results = json.loads(r.text)
                    for target_template in target_template_results:
                        if re.fullmatch(template_name, target_template['name']):
                            logger.debug("Template name: %s already exists on server %s"
                                         % (target_template['name'], target_server_address))
                            if target_template_name_exists:
                                logger.fatal(
                                    "Multiple templates matched name %s on server %s. "
                                    "Import can only be used for single template." % (
                                        template_name, target_server_address))
                                raise ProxyError()
                            else:
                                target_template_name_to_delete = target_template
                                target_template_name_exists = True
                    if target_template_name_exists:
                        if args.force:
                            print("#### Forcing deletion of template name %s on server: %s" % (
                                target_template_name_to_delete['name'], target_server_address))

                            logger.debug("Deleting template name %s on server: %s"
                                         % (target_template_name_to_delete['name'], target_server_address))
                            r = requests.delete(
                                base_dst_api_url + '/templates/' + target_template_name_to_delete['template_id'],
                                auth=(username, password))
                            if not r.status_code == 204:
                                if r.status_code == 404:
                                    logger.debug("Template did not exist before, not deleted")
                                else:
                                    logger.fatal("unable to delete template")
                                    raise ProxyError()
                            else:
                                print("#### Deleted template name %s on server: %s"
                                      % (target_template_name_to_delete['name'], target_server_address))
                        else:
                            logger.fatal(
                                "Template name: %s already exists on server %s. Use --force to overwrite it"
                                " during import."
                                % (target_template_name_to_delete['name'], target_server_address))
                            raise ProxyError()

                    logger.debug("Checking if target template id exists...")
                    url = base_dst_api_url + '/templates'
                    r = requests.get(url, auth=(username, password))
                    if r.status_code == 200:
                        target_template_id_exists = False
                        target_template_id_to_delete = None
                        target_template_results = json.loads(r.text)
                        for target_template in target_template_results:
                            if re.fullmatch(template_id, target_template['template_id']):
                                logger.debug("Template id: %s already exists on server %s"
                                             % (target_template['template_id'], target_server_address))
                                if target_template_id_exists:
                                    logger.fatal(
                                        "Multiple templates matched id %s on server %s. "
                                        "Import can only be used for single template." % (
                                            template_id, target_server_address))
                                    raise ProxyError()
                                else:
                                    target_template_id_to_delete = target_template
                                    target_template_id_exists = True
                        if target_template_id_exists:
                            if args.force:
                                print("#### Forcing deletion of template id %s on server: %s" % (
                                    target_template_id_to_delete['template_id'], target_server_address))

                                logger.debug("Deleting template id %s on server: %s"
                                             % (target_template_id_to_delete['template_id'], target_server_address))
                                r = requests.delete(
                                    base_dst_api_url + '/templates/' + target_template_id_to_delete['template_id'],
                                    auth=(username, password))
                                if not r.status_code == 204:
                                    if r.status_code == 404:
                                        logger.debug("Template did not exist before, not deleted")
                                    else:
                                        logger.fatal("unable to delete template")
                                        raise ProxyError()
                                else:
                                    print("#### Deleted template id %s on server: %s"
                                          % (target_template_id_to_delete['template_id'], target_server_address))
                            else:
                                logger.fatal(
                                    "Template id: %s already exists on server %s. Use --force to overwrite it"
                                    " during import."
                                    % (target_template_id_to_delete['template_id'], target_server_address))
                                raise ProxyError()

                    logger.debug("Importing template")
                    # import template
                    url = base_dst_api_url + '/templates'
                    headers = {'content-type': 'application/json'}
                    r = requests.post(url, auth=(username, password),
                                      data=json.dumps(template, sort_keys=True, indent=4), verify=False,
                                      headers=headers)
                    if not r.status_code == 201:
                        if r.status_code == 403:
                            logger.fatal("Forbidden to import template on target server.")
                            raise ProxyError()
                        else:
                            logger.fatal("Unable to import template on target server. Response: %s " % r.content)
                            raise ProxyError()
                    else:
                        print("#### Template %s replicated from server: %s to server: %s"
                              % (template_name, src_server, target_server_address))

                else:
                    logger.fatal("Could not get status of templates from server %s." % target_server_address)
                    raise ProxyError()

        print("Done.")

    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
