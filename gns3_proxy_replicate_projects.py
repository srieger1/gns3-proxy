#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
    gns3_proxy_replicate_projects

    Replication of GNS3 projects across multiple backend nodes, e.g., behind a GNS3 proxy

    :copyright: (c) 2019 by Sebastian Rieger.
    :license: BSD, see LICENSE for more details.
"""

# TODO: replicate images/appliances? symbols/templates etc.?

import argparse
import configparser
import json
import logging
import re
import shutil
import sys
from ipaddress import ip_address
import random
import datetime
import tempfile
import requests

VERSION = (0, 2)
__version__ = '.'.join(map(str, VERSION[0:2]))
__description__ = 'GNS3 Proxy Replicate Projects'
__author__ = 'Sebastian Rieger'
__author_email__ = 'sebastian@riegers.de'
__homepage__ = 'https://github.com/srieger1/gns3-proxy'
__download_url__ = '%s/archive/master.zip' % __homepage__
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
DEFAULT_DELETE_TARGET_PROJECT = False
DEFAULT_INJECT_REPLICATION_NOTE = False
DEFAULT_LOG_LEVEL = 'INFO'
DEFAULT_FORCE = False


class ProxyError(Exception):
    pass


def parse_args(args):
    parser = argparse.ArgumentParser(
        description='gns3_proxy_replicate_projects.py v%s Replicate project to GNS3 proxy backends.' % __version__,
        epilog='gns3_proxy not working? Report at: %s/issues/new' % __homepage__
    )
    # Argument names are ordered alphabetically.
    parser.add_argument('--config-file', type=str, default=DEFAULT_CONFIG_FILE,
                        help='Location of the gns3_proxy config file. Default: gns3_proxy_config.ini.')
    parser.add_argument('--delete-target-project', action='store_true', default=DEFAULT_DELETE_TARGET_PROJECT,
                        help='Whether to delete target project before import.'
                             'By default project will not be deleted on target server, if it already exists.')
    parser.add_argument('--force', action='store_true', default=DEFAULT_FORCE,
                        help='Force action without further prompt. E.g., overwrite or delete existing projects '
                             'without further verification.')
    parser.add_argument('--inject-replication-note', action='store_true', default=DEFAULT_INJECT_REPLICATION_NOTE,
                        help='Whether to inject a note containing the target server name and additional replication'
                             'details in the target project.')
    parser.add_argument('--log-level', type=str, default=DEFAULT_LOG_LEVEL,
                        help='Valid options: DEBUG, INFO (default), WARNING, ERROR, CRITICAL. '
                             'Both upper and lowercase values are allowed.'
                             'You may also simply use the leading character e.g. --log-level d')
    project_group = parser.add_mutually_exclusive_group(required=True)
    project_group.add_argument('--project-id', type=str,
                               help='Project UUID to copy.')
    project_group.add_argument('--project-name', type=str,
                               help='Project name to copy. Can be specified as a regular expression to match '
                                    'multiple projects.')
    parser.add_argument('--regenerate-mac-address', type=str,
                        help='Specify a mac address that should be regenerated in the replicated target project. '
                             'This is, e.g., necessary for interfaces using DHCP to an external network, i.e., '
                             'cloud nodes in GNS3, to avoid MAC and IP address conflicts.')
    parser.add_argument('--source-server', type=str, required=True,
                        help='Source server to copy project from. A name of a server/backend defined in the '
                             'config file.')
    parser.add_argument('--target-server', type=str, required=True,
                        help='Target(s) to copy project to. Name of a servers/backends defined in the config file. '
                             'Can be specified as a regular expression to match multiple target servers.')

    return parser.parse_args(args)


def main():
    replication_timestamp = datetime.datetime.now()

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
        for key, value in server_items:
            try:
                ip_address(value)
            except ValueError:
                logger.fatal("server config %s is not a valid IP address (e.g., 1.2.3.4)" % value)
                raise ProxyError()
            config_servers[key] = value
    else:
        config_servers = None

    logger.debug("Config backend_user: %s" % backend_user)
    logger.debug("Config backend_password: %s" % backend_password)
    logger.debug("Config backend_port: %s" % backend_port)
    logger.debug("Config default_server: %s" % config_servers)

    logger.debug("Config servers: %s" % config_servers)

    try:
        username = backend_user
        password = backend_password

        # source handling

        # get source server IP
        if args.source_server in config_servers:
            src_server = config_servers[args.source_server]
            logger.debug("Source server will be %s:%s" % (
                src_server, backend_port))
        else:
            logger.fatal("Source server not found in config.")
            raise ProxyError()

        base_src_api_url = "http://" + src_server + ":" + str(backend_port) + "/v2"

        logger.debug("Searching source project UUIDs")
        projects = list()
        url = base_src_api_url + '/projects'
        r = requests.get(url, auth=(username, password))
        if not r.status_code == 200:
            logger.fatal("Could not list projects.")
            raise ProxyError()
        else:
            project_results = json.loads(r.text)
            for project in project_results:
                if args.project_id:
                    if args.project_id == project['project_id']:
                        logger.debug('matched UUID of: %s' % project)
                        projects.append(project)
                else:
                    if re.fullmatch(args.project_name, project['name']):
                        logger.debug('matched name of: %s' % project)
                        projects.append(project)

        if len(projects) == 0:
            logger.fatal("Specified project not found.")
            raise ProxyError()

        for project in projects:
            project_uuid = project['project_id']
            print("#### Replicating project: %s" % project_uuid)
            tmp_file = tempfile.TemporaryFile()

            # close source project
            logger.debug("Closing source project")
            url = base_src_api_url + '/projects/' + project_uuid + "/close"
            data = "{}"
            r = requests.post(url, data, auth=(username, password))
            if not r.status_code == 201:
                logger.fatal("Unable to close source project. Source project does not exist or is corrupted?")
                raise ProxyError()

            # export source project
            logger.debug("Exporting source project")
            url = base_src_api_url + '/projects/' + project_uuid + "/export"
            r = requests.get(url, stream=True, auth=(username, password))
            if r.status_code == 200:
                r.raw.decode_content = True
                shutil.copyfileobj(r.raw, tmp_file)
                logger.debug("Project exported to file: %s" % tmp_file.name)
            else:
                logger.fatal("Unable to export project from source server.")
                raise ProxyError()

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
                print("    #### Replicating project: %s to server: %s" % (project_uuid, target_server_address))
                base_dst_api_url = "http://" + target_server_address + ":" + str(backend_port) + "/v2"

                logger.debug("Checking if target project exists...")
                url = base_dst_api_url + '/projects/' + project_uuid
                r = requests.get(url, auth=(username, password))
                if r.status_code == 200:
                    if args.force:
                        print("         Project UUID: %s already exists on server: %s overwriting it."
                              % (project_uuid, target_server_address))
                    else:
                        print("         WARNING: Project UUID: %s already exists on server: %s. Use --force"
                              " to overwrite." % (project_uuid, target_server_address))
                        continue

                # close destination project
                logger.debug("Closing destination project")
                url = base_dst_api_url + '/projects/' + project_uuid + "/close"
                data = "{}"
                r = requests.post(url, data, auth=(username, password))
                if not r.status_code == 201:
                    if r.status_code == 404:
                        logger.debug("Destination project did not exist before, not closed")
                    else:
                        raise ProxyError()

                if args.delete_target_project:
                    if args.force:
                        logger.debug("Deleting destination project")
                        r = requests.delete(base_dst_api_url + '/projects/' + project_uuid, auth=(username, password))
                        if not r.status_code == 204:
                            if r.status_code == 404:
                                logger.debug("Destination project did not exist before, not deleted")
                            else:
                                logger.fatal("unable to delete project")
                                raise ProxyError()
                    else:
                        print("        WARNING: Project UUID %s to delete found on server: %s, use --force to really "
                              "remove it." % (project_uuid, target_server_address))
                        continue

                logger.debug("Importing destination project")
                # import project
                url = base_dst_api_url + '/projects/' + project_uuid + "/import"
                tmp_file.seek(0)
                files = {'file': tmp_file}
                r = requests.post(url, files=files, auth=(username, password))
                if not r.status_code == 201:
                    if r.status_code == 403:
                        logger.fatal("Forbidden to import project on target server.")
                        raise ProxyError()
                    else:
                        logger.fatal("Unable to import project on target server.")
                        raise ProxyError()

                if args.regenerate_mac_address or args.inject_replication_note:
                    # open target project
                    logger.debug("Open imported project to make changes.")
                    url = base_dst_api_url + '/projects/' + project_uuid + "/open"
                    data = "{}"
                    r = requests.post(url, data, auth=(username, password))
                    if not r.status_code == 201:
                        logger.fatal("Unable to open imported project on target server.")
                        raise ProxyError()

                # check if we need to change MAC addresses in the target project
                if args.regenerate_mac_address:
                    logger.debug("Trying to regenerate specified MAC addresses in target project.")

                    # getting target project nodes and search for mac address changes
                    logger.debug("Getting destination project nodes")
                    url = base_dst_api_url + '/projects/' + project_uuid + "/nodes"
                    r = requests.get(url, auth=(username, password))
                    if not r.status_code == 200:
                        logger.fatal("Unable to get nodes from imported project on target server.")
                        raise ProxyError()
                    else:
                        nodes = json.loads(r.text)
                        for node in nodes:
                            if 'properties' in node:
                                if 'mac_address' in node['properties']:
                                    if re.fullmatch(args.regenerate_mac_address,
                                                    node['properties']['mac_address']):
                                        logger.debug('Found MAC address that needs to be changed: %s using match: %s'
                                                     % (node['properties']['mac_address'],
                                                        args.regenerate_mac_address))
                                        mac_address = "02:01:00:%02x:%02x:%02x" % (random.randint(0, 255),
                                                                                   random.randint(0, 255),
                                                                                   random.randint(0, 255))
                                        # changing mac address in target project node
                                        print("         Changing mac address of node: %s from: %s to: %s"
                                                    % (node['name'], node['properties']['mac_address'], mac_address))
                                        url = base_dst_api_url + '/projects/' + project_uuid + "/nodes/" \
                                              + node['node_id']
                                        data = '{ "properties": { "mac_address": "' + mac_address + '" } }'
                                        r = requests.put(url, data, auth=(username, password))
                                        if not r.status_code == 200:
                                            logger.fatal("Unable to change mac address for node: %s in target project."
                                                         % node['name'])
                                            raise ProxyError()

                # check if we need to inject a note describing the replication details in the project
                if args.inject_replication_note:
                    logger.debug("Trying to add note describing replication details in the target project.")

                    # open target project
                    logger.debug("Open imported project.")
                    url = base_dst_api_url + '/projects/' + project_uuid + "/open"
                    data = "{}"
                    r = requests.post(url, data, auth=(username, password))
                    if not r.status_code == 201:
                        logger.fatal("Unable to open imported project on target server.")
                        raise ProxyError()

                    # adding note describing the replication details
                    logger.debug("Adding a note describing the replication details to the target project.")
                    url = base_dst_api_url + '/projects/' + project_uuid + "/drawings"
                    data = '{ "x": 0, "y": 0, "z": 0, ' \
                           '"svg": "<svg height=\\"24\\" width=\\"100\\">' \
                           '<text fill=\\"#000000\\" fill-opacity=\\"1.0\\" font-family=\\"TypeWriter\\"' \
                           ' font-size=\\"10.0\\" font-weight=\\"bold\\">' \
                           'Server: ' + str(target_server_address).replace('"', '\\"') + '\\n' \
                           'Replicated from: ' + str(args.source_server).replace('"', '\\"') + ' ' \
                           'Project: ' + str(args.project).replace('"', '\\"') + '\\n' \
                           'Replicated at: ' + str(replication_timestamp).replace('"', '\\"') + '\\n' \
                           '</text></svg>" }'
                    r = requests.post(url, data, auth=(username, password))
                    if not r.status_code == 201:
                        logger.fatal("Unable to inject a note describing the replication details in the project")
                        raise ProxyError()

                if args.regenerate_mac_address or args.inject_replication_note:
                    # close target project
                    logger.debug("Close imported project.")
                    url = base_dst_api_url + '/projects/' + project_uuid + "/close"
                    data = "{}"
                    r = requests.post(url, data, auth=(username, password))
                    if not r.status_code == 201:
                        logger.fatal("Unable to close imported project on target server.")
                        raise ProxyError()

            # project is replicated close temp file
            tmp_file.close()

        print("Done.")

    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
