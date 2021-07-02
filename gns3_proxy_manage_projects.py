#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
    gns3_proxy_manage_projects

    Management of GNS3 projects across multiple backend nodes, e.g., behind a GNS3 proxy

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
import shutil
import uuid
from ipaddress import ip_address

import requests

VERSION = (0, 3)
__version__ = '.'.join(map(str, VERSION[0:2]))
__description__ = 'GNS3 Proxy Manage Projects'
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
DEFAULT_START_ACTION = False
DEFAULT_STOP_ACTION = False
DEFAULT_DELETE_ACTION = False
DEFAULT_DUPLICATE_ACTION = False
DEFAULT_SHOW_ACTION = False
DEFAULT_FORCE = False
DEFAULT_INCLUDE_BASE_IMAGES = False
DEFAULT_INCLUDE_SNAPSHOTS = False
DEFAULT_RESET_MAC_ADDRESSES = False


class ProxyError(Exception):
    pass


def parse_args(args):
    parser = argparse.ArgumentParser(
        description='gns3_proxy_manage_projects.py v%s Manage projects on GNS3 proxy backends.' % __version__,
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
                        help='Force action without further prompt. E.g., delete projects without further verification.')

    project_group = parser.add_mutually_exclusive_group(required=True)
    project_group.add_argument('--project-id', type=str,
                               help='Project UUID to copy. During import used as the UUID for the new project.')
    project_group.add_argument('--project-name', type=str,
                               help='Project name to copy. Can be specified as a regular expression to match '
                                    'multiple projects.')

    parser.add_argument('--include-base-images', action='store_true', default=DEFAULT_INCLUDE_BASE_IMAGES,
                        help='Include base images in the export used to replicate the project.')
    parser.add_argument('--include-snapshots', action='store_true', default=DEFAULT_INCLUDE_SNAPSHOTS,
                        help='Force action without further prompt. E.g., overwrite or delete existing projects '
                             'without further verification.')
    parser.add_argument('--reset-mac-addresses', action='store_true', default=DEFAULT_RESET_MAC_ADDRESSES,
                        help='Reset all mac addresses in the exported project.')
    parser.add_argument('--compression', type=str, default='zip',
                        help='Compress exported project used for the replication. Possible values \'zip\', \'bzip2\''
                             '\'lzma\', \'none\'.')

    parser.add_argument('--duplicate-name', type=str,
                        help='Name to use as a prefix for the duplicated project. An ascending number from'
                             '--duplicate-start to --duplicate-end will be appended to the project name.')
    parser.add_argument('--duplicate-start', type=int, default=1,
                        help='Start numbering of duplicates using the specified number.')
    parser.add_argument('--duplicate-end', type=int, default=1,
                        help='End numbering of duplicates using the specified number.')
    parser.add_argument('--duplicates-per-target-server', type=int, default=0,
                        help='Number of duplicates per server. Distribute duplicates across matched target servers.')

    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument('--export-to-dir', type=str,
                              help='Export projects to directory.')
    action_group.add_argument('--import-from-file', type=str,
                              help='Import project from file.')
    action_group.add_argument('--show', action='store_true', default=DEFAULT_SHOW_ACTION,
                              help='Show projects and their status.')
    action_group.add_argument('--delete', action='store_true', default=DEFAULT_DELETE_ACTION,
                              help='Delete projects.')
    action_group.add_argument('--duplicate', action='store_true', default=DEFAULT_DUPLICATE_ACTION,
                              help='Duplicate projects.')
    action_group.add_argument('--start', action='store_true', default=DEFAULT_START_ACTION,
                              help='Start projects.')
    action_group.add_argument('--stop', action='store_true', default=DEFAULT_STOP_ACTION,
                              help='Start projects.')

    parser.add_argument('--target-server', type=str, required=True,
                        help='Target(s) to manage projects on. Name of a servers/backends defined in the config file. '
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
            base_dst_api_url = None
            duplicate_number = args.duplicate_start
            for server in config_servers:
                if re.fullmatch(args.target_server, server):
                    logger.debug("Target server found: %s (%s) using provided match: %s" % (server,
                                                                                            config_servers[server],
                                                                                            args.target_server))
                    # build target server API URL
                    base_dst_api_url = "http://" + config_servers[server] + ":" + str(backend_port) + "/v2"

                    if args.import_from_file:
                        if args.project_name:
                            logger.fatal("Import can only be used in combination with --project-id argument specifying "
                                         "a UUID for the new project on the target server.")
                            raise ProxyError()
                        else:
                            try:
                                project_uuid = str(uuid.UUID(args.project_id, version=4))
                            except ValueError:
                                logger.fatal("Provided project-id %s is not a valid UUID4 (like, e.g., "
                                             "f1d1e2b8-c41f-42cf-97d4-513f3fd01cd2)." % args.project_id)
                                raise ProxyError()

                        logger.debug("Checking if target project exists...")
                        url = base_dst_api_url + '/projects/' + project_uuid
                        r = requests.get(url, auth=(username, password))
                        if r.status_code == 200:
                            logger.debug("Project UUID: %s already exists on server: %s."
                                         % (project_uuid, config_servers[server]))

                            if args.force:
                                # close destination project
                                logger.debug("Closing destination project")
                                url = base_dst_api_url + '/projects/' + project_uuid + "/close"
                                data = "{}"
                                r = requests.post(url, data, auth=(username, password))
                                if not r.status_code == 201 and not r.status_code == 204:
                                    if r.status_code == 404:
                                        logger.debug("Project did not exist before, not closed")
                                    else:
                                        raise ProxyError()

                                # deleting project
                                print("Deleting existing project UUID %s on server: %s"
                                      % (project_uuid, config_servers[server]))
                                r = requests.delete(base_dst_api_url + '/projects/' + project_uuid,
                                                    auth=(username, password))
                                if not r.status_code == 204:
                                    if r.status_code == 404:
                                        logger.debug("Project did not exist before, not deleted")
                                    else:
                                        logger.fatal("unable to delete project")
                                        logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                                        raise ProxyError()

                            else:
                                logger.fatal("    WARNING: Project UUID: %s already exists on server: %s import "
                                             "failed, use --force to overwrite."
                                             % (project_uuid, config_servers[server]))
                                continue

                        logger.debug("Importing project")
                        # import project
                        url = base_dst_api_url + '/projects/' + project_uuid + "/import"
                        files = {'file': open(args.import_from_file, 'rb')}
                        r = requests.post(url, files=files, auth=(username, password))
                        if not r.status_code == 201:
                            if r.status_code == 403:
                                logger.fatal("Forbidden to import project on target server.")
                                logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                                raise ProxyError()
                            else:
                                logger.fatal("Unable to import project on target server.")
                                logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                                raise ProxyError()
                        else:
                            print("#### Project %s imported from file: %s on server: %s"
                                  % (project_uuid, args.import_from_file, server))
                    else:
                        logger.debug("Searching target project UUIDs")
                        projects = list()
                        url = base_dst_api_url + '/projects'
                        r = requests.get(url, auth=(username, password))
                        if not r.status_code == 200:
                            logger.fatal("Could not list projects.")
                            logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
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
                            if args.export_to_dir:
                                # Closing project
                                logger.debug("Closing project")
                                url = base_dst_api_url + '/projects/' + project_uuid + "/close"
                                data = "{}"
                                r = requests.post(url, data, auth=(username, password))
                                if not r.status_code == 201 and not r.status_code == 204:
                                    logger.fatal("Unable to close project. Project does not exist or is corrupted?")
                                    logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                                    raise ProxyError()

                                # export project
                                logger.debug("Exporting project")
                                url = base_dst_api_url + '/projects/' + project_uuid + "/export?"
                                if args.include_base_images:
                                    url = url + "include_images=yes"
                                else:
                                    url = url + "include_images=no"
                                if args.include_snapshots:
                                    url = url + "&include_snapshots=yes"
                                else:
                                    url = url + "&include_snapshots=no"
                                if args.reset_mac_addresses:
                                    url = url + "&reset_mac_addresses=yes"
                                else:
                                    url = url + "&reset_mac_addresses=no"
                                url = url + "&compression=" + args.compression
                                r = requests.get(url, stream=True, auth=(username, password))
                                if r.status_code == 200:
                                    r.raw.decode_content = True
                                    filename = str(server) + "_" + project['name'] + "_" + project_uuid + "_" + \
                                               time.strftime("%Y%m%d-%H%M%S") + "." + args.compression
                                    shutil.copyfileobj(r.raw, open(os.path.join(args.export_to_dir, filename), 'wb'))
                                    print("#### Project %s (%s) exported to file: %s (%s bytes) from server: %s"
                                          % (project['name'], project['project_id'], filename,
                                             os.stat(os.path.join(args.export_to_dir, filename)).st_size, server))
                                else:
                                    logger.fatal("Unable to export project from source server.")
                                    logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                                    raise ProxyError()

                            if args.delete:
                                if args.force:
                                    # close destination project
                                    logger.debug("Closing destination project")
                                    url = base_dst_api_url + '/projects/' + project_uuid + "/close"
                                    data = "{}"
                                    r = requests.post(url, data, auth=(username, password))
                                    if not r.status_code == 201 and not r.status_code == 204:
                                        if r.status_code == 404:
                                            logger.debug("Project did not exist before, not closed")
                                        else:
                                            raise ProxyError()

                                    # deleting project
                                    print("#### Deleting project UUID %s on server: %s"
                                          % (project_uuid, config_servers[server]))
                                    r = requests.delete(base_dst_api_url + '/projects/' + project_uuid,
                                                        auth=(username, password))
                                    if not r.status_code == 204:
                                        if r.status_code == 404:
                                            logger.debug("Project did not exist before, not deleted")
                                        else:
                                            logger.fatal("unable to delete project")
                                            logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                                            raise ProxyError()
                                else:
                                    print("    WARNING: Project UUID %s to delete found on server: %s, use --force"
                                          " to really remove it." % (project_uuid, config_servers[server]))

                            if args.duplicate:
                                project_name = project["name"]
                                if args.duplicates_per_target_server > 0:
                                    duplicates_created_on_server = 0
                                else:
                                    duplicate_number = args.duplicate_start
                                while duplicate_number <= args.duplicate_end:
                                    if args.duplicate_name is not None:
                                        duplicate_project_name = args.duplicate_name + str(duplicate_number)
                                    else:
                                        duplicate_project_name = project_name + str(duplicate_number)
                                    print("#### Duplicating project %s (%s) on server: %s, new name: %s"
                                          % (project_name, project_uuid, config_servers[server],
                                             duplicate_project_name))
                                    url = base_dst_api_url + '/projects/' + project_uuid + "/duplicate"
                                    json_data = {'name': duplicate_project_name,
                                                 'reset_mac_addresses': args.reset_mac_addresses}
                                    data = json.dumps(json_data)
                                    r = requests.post(url, data, auth=(username, password))
                                    if not r.status_code == 201:
                                        logger.fatal("Unable to duplicate project on target server.")
                                        logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                                        raise ProxyError()
                                    duplicate_number = duplicate_number + 1
                                    if args.duplicates_per_target_server > 0:
                                        duplicates_created_on_server = duplicates_created_on_server + 1
                                        if duplicates_created_on_server >= args.duplicates_per_target_server:
                                            break

                            if args.show:
                                print("#### Server: %s, Project Name: %s, Project_ID: %s, Status: %s, "
                                      % (server, project['name'], project['project_id'], project['status']))

                            if args.start:
                                print(
                                    "#### Opening and starting project: %s on %s" % (project['project_id'], server))

                                # Opening project
                                logger.debug("Opening target project")
                                url = base_dst_api_url + '/projects/' + project['project_id'] + "/open"
                                data = "{}"
                                r = requests.post(url, data, auth=(username, password))
                                if not r.status_code == 201:
                                    logger.fatal("Unable to open project on target server.")
                                    logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                                    raise ProxyError()

                                # Starting project
                                logger.debug("Starting destination project")
                                url = base_dst_api_url + '/projects/' + project['project_id'] + "/nodes/start"
                                data = "{}"
                                r = requests.post(url, data, auth=(username, password))
                                if not r.status_code == 204:
                                    logger.fatal("Unable to start project on target server.")
                                    logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                                    raise ProxyError()

                            if args.stop:
                                print(
                                    "#### Stopping and closing project: %s on %s" % (project['project_id'], server))

                                # Stopping project
                                logger.debug("Stopping destination project")
                                url = base_dst_api_url + '/projects/' + project['project_id'] + "/nodes/stop"
                                data = "{}"
                                r = requests.post(url, data, auth=(username, password))
                                if not r.status_code == 204:
                                    logger.fatal("Unable to stop project on target server.")
                                    logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                                    raise ProxyError()

                                # Closing project
                                logger.debug("Closing project")
                                url = base_dst_api_url + '/projects/' + project['project_id'] + "/close"
                                data = "{}"
                                r = requests.post(url, data, auth=(username, password))
                                if not r.status_code == 201 and not r.status_code == 204:
                                    logger.fatal("Unable to close project. Project does not exist or is corrupted?")
                                    logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                                    raise ProxyError()

            if base_dst_api_url is None:
                logger.fatal("Could not find target server %s." % args.target_server)
                raise ProxyError()

        print("Done.")

    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
