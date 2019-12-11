#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
    gns3_proxy_manage_images

    Replication of GNS3 images across multiple backend nodes, e.g., behind a GNS3 proxy

    :copyright: (c) 2019 by Sebastian Rieger.
    :license: BSD, see LICENSE for more details.
"""

import argparse
import configparser
import json
import logging
import re
import shutil
import sys
import tempfile
from ipaddress import ip_address

import requests

VERSION = (0, 1)
__version__ = '.'.join(map(str, VERSION[0:2]))
__description__ = 'GNS3 Proxy Replicate Images'
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
DEFAULT_LOG_LEVEL = 'INFO'
DEFAULT_FORCE = False

# Compute Image Backend
IMAGE_BACKEND_URL = '/compute/qemu/images'

# Alternate location for image access, used for upload by the GNS3 client, but download (GET) throws an error
ALT_IMAGE_BACKEND_URL = '/computes/local/qemu/images'


class ProxyError(Exception):
    pass


def parse_args(args):
    parser = argparse.ArgumentParser(
        description='gns3_proxy_replicate_images.py v%s Replicates images on GNS3 proxy backends.' % __version__,
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
                        help='Force action without further prompt. E.g., delete images without further '
                             'verification.')

    parser.add_argument('--image-filename', type=str, required=True,
                        help='Name of the image to be replicated.'
                             'Can be specified as a regular expression to match multiple images.')

    parser.add_argument('--source-server', type=str, required=True,
                        help='Source server to copy images from. A name of a server/backend defined in the '
                             'config file.')
    parser.add_argument('--target-server', type=str, required=True,
                        help='Target(s) to copy images to. Name of a servers/backends defined in the config file. '
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
        logger.debug("Searching source images")
        images = list()
        url = base_src_api_url + IMAGE_BACKEND_URL

        r = requests.get(url, auth=(username, password))
        if not r.status_code == 200:
            logger.fatal("Could not list images.")
            raise ProxyError()
        else:
            image_results = json.loads(r.text)
            for image in image_results:
                if re.fullmatch(args.image_filename, image['filename']):
                    logger.debug('matched image: %s' % image['filename'])
                    images.append(image)

        if len(images) == 0:
            logger.fatal("Specified image not found.")
            raise ProxyError()

        for image in images:
            image_filename = image['filename']
            print("#### Replicating image: %s" % image_filename)
            tmp_file = tempfile.TemporaryFile()

            # export source image
            logger.debug("Exporting source image")
            url = base_src_api_url + IMAGE_BACKEND_URL + '/' + image_filename
            r = requests.get(url, stream=True, auth=(username, password))
            if r.status_code == 200:
                r.raw.decode_content = True
                shutil.copyfileobj(r.raw, tmp_file)
                logger.debug("Image exported to file: %s" % tmp_file.name)
            else:
                logger.fatal("Unable to export image from source server.")
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
                logger.debug("    #### Replicating image: %s to server: %s" % (image_filename, target_server_address))
                base_dst_api_url = "http://" + target_server_address + ":" + str(backend_port) + "/v2"

                logger.debug("Checking if target image exists...")
                url = base_dst_api_url + IMAGE_BACKEND_URL
                r = requests.get(url, auth=(username, password))
                if r.status_code == 200:
                    target_image_exists = False
                    target_image_to_delete = ''
                    target_image_results = json.loads(r.text)
                    for target_image in target_image_results:
                        if re.fullmatch(image_filename, target_image['filename']):
                            logger.debug("image: %s already exists on server %s"
                                         % (target_image['filename'], target_server_address))
                            if target_image_exists:
                                logger.fatal(
                                    "Multiple images matched %s on server %s. "
                                    "Import can only be used for single image." % (
                                        image_filename, target_server_address))
                                raise ProxyError()
                            else:
                                target_image_exists = True
                                target_image_to_delete = image['filename']

                    if target_image_exists:
                        if args.force:
                            # deleting image
                            # print("Deleting existing image %s on server: %s"
                            #      % (image_to_delete, config_servers[server]))
                            # url = base_dst_api_url + IMAGE_BACKEND_URL + '/' + image_to_delete
                            # r = requests.delete(url, auth=(username, password))
                            # if not r.status_code == 204:
                            #    if r.status_code == 404:
                            #        logger.debug("Image did not exist before, not deleted")
                            #    else:
                            #        logger.fatal("unable to delete image")
                            #        raise ProxyError()
                            logger.debug(
                                "image: %s (%s) already exists on server %s. Overwriting it."
                                % (image_filename, target_image_to_delete, target_server_address))
                        else:
                            logger.fatal(
                                "image: %s (%s) already exists on server %s. Use --force to overwrite it"
                                " during import."
                                % (image_filename, target_image_to_delete, target_server_address))
                            raise ProxyError()

                    logger.debug("Importing image")
                    # import image
                    url = base_dst_api_url + ALT_IMAGE_BACKEND_URL + '/' + image_filename
                    tmp_file.seek(0)
                    files = {'file': tmp_file}
                    r = requests.post(url, files=files, auth=(username, password))
                    if not r.status_code == 200:
                        if r.status_code == 403:
                            logger.fatal("Forbidden to import image on target server.")
                            raise ProxyError()
                        else:
                            logger.fatal("Unable to import image on target server.")
                            raise ProxyError()
                    else:
                        print("    #### image %s replicated from server: %s to server: %s"
                              % (image_filename, src_server, target_server_address))
                else:
                    logger.fatal("Could not get status of images from server %s." % target_server_address)
                    raise ProxyError()

            # image is replicated close temp file
            tmp_file.close()

        print("Done.")

    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
