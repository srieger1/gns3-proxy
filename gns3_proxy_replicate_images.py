#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
    gns3_proxy_manage_images

    Replication of GNS3 images across multiple backend nodes, e.g., behind a GNS3 proxy

    :copyright: (c) 2020 by Sebastian Rieger.
    :license: BSD, see LICENSE for more details.
"""

import argparse
import configparser
import json
import logging
import re
import sys
import time
from ipaddress import ip_address

from requests_toolbelt.streaming_iterator import StreamingIterator
import requests

VERSION = (0, 5)
__version__ = '.'.join(map(str, VERSION[0:2]))
__description__ = 'GNS3 Proxy Replicate Images'
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

    parser.add_argument('--image-type', type=str, required=True, choices=['qemu', 'dynamips', 'iou'],
                        help='Type of the images to be managed.'
                             'GNS3 currently uses different API for each image type.')

    parser.add_argument('--image-filename', type=str, required=True,
                        help='Name of the image to be replicated.'
                             'Can be specified as a regular expression to match multiple images.')

    parser.add_argument('--buffer', type=int, required=False, default=8192,
                        help='Number of bytes to use for buffering download and upload of images.')

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

    # Compute Image Backend
    image_backend_url = '/compute/' + args.image_type + '/images'

    # Alternate location for image access, used for upload by the GNS3 client, but download (GET) throws an error
    alt_image_backend_url = '/computes/local/' + args.image_type + '/images'

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
        url = base_src_api_url + image_backend_url

        r = requests.get(url, auth=(username, password))
        if not r.status_code == 200:
            logger.fatal("Could not list images.")
            logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
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
            print("#### Replicating image: %s from server: %s (%s)" % (image_filename, args.source_server, src_server))

            # target handling

            # Try to find match for target server in config
            target_servers = list()
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
                            target_servers.append({'name': key, 'address': config_servers[key]})
            else:
                logger.fatal("No servers defined in config. Could not select target server.")
                raise ProxyError()

            if len(target_servers) == 0:
                logger.fatal("No target servers found using match: %s. Could not select target server."
                             % args.target_server)
                raise ProxyError()

            for target_server in target_servers:
                target_server_name = target_server['name']
                target_server_address = target_server['address']
                logger.debug("    Replicating image: %s to server: %s" % (image_filename, target_server_name))
                base_dst_api_url = "http://" + target_server_address + ":" + str(backend_port) + "/v2"

                logger.debug("Checking if target image exists...")
                url = base_dst_api_url + image_backend_url
                r = requests.get(url, auth=(username, password))
                if r.status_code == 200:
                    target_image_exists = False
                    target_image_md5sum = ''
                    target_image_to_delete = ''
                    target_image_results = json.loads(r.text)
                    for target_image in target_image_results:
                        if re.fullmatch(image_filename, target_image['filename']):
                            logger.debug("image: %s already exists on server %s"
                                         % (target_image['filename'], target_server_name))
                            if target_image_exists:
                                logger.fatal(
                                    "Multiple images matched %s on server %s. "
                                    "Import can only be used for single image." % (
                                        image_filename, target_server_name))
                                raise ProxyError()
                            else:
                                target_image_exists = True
                                target_image_md5sum = target_image['md5sum']
                                target_image_to_delete = image['filename']

                    if target_image_exists:
                        if args.force:
                            # deleting image
                            # print("Deleting existing image %s on server: %s"
                            #      % (image_to_delete, config_servers[server]))
                            # url = base_dst_api_url + image_backend_url + '/' + image_to_delete
                            # r = requests.delete(url, auth=(username, password))
                            # if not r.status_code == 204:
                            #    if r.status_code == 404:
                            #        logger.debug("Image did not exist before, not deleted")
                            #    else:
                            #        logger.fatal("unable to delete image")
                            #        raise ProxyError()
                            logger.debug(
                                "image: %s (%s) already exists on server %s. Overwriting it."
                                % (image_filename, target_image_to_delete, target_server_name))
                        elif image['md5sum'] == target_image_md5sum:
                            logger.debug(
                                "image: %s (%s) already exists on server %s, skipping transfer. "
                                "Use --force to overwrite it during import."
                                % (image_filename, target_image_to_delete, target_server_name))
                            continue
                        else:
                            logger.fatal(
                                "image: %s (%s) already exists on server, but the md5sum does not match."
                                "on target %s. Use --force to overwrite it during import."
                                % (image_filename, target_image_to_delete, target_server_name))
                            raise ProxyError()

                    # export source image
                    logger.debug("Opening source image")
                    url = base_src_api_url + image_backend_url + '/' + image_filename
                    r_export = requests.get(url, stream=True, auth=(username, password))
                    if not r_export.status_code == 200:
                        logger.fatal("Unable to export image from source server.")
                        logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                        raise ProxyError()

                    start_timestamp = int(round(time.time()))

                    def generate_chunk():
                        transferred_length_upload = 0
                        prev_transferred_length_upload = 0
                        next_percentage_to_print_upload = 0
                        prev_timestamp_upload = int(round(time.time() * 1000))
                        for in_chunk in r_export.iter_content(chunk_size=args.buffer):
                            if in_chunk:
                                yield in_chunk
                                transferred_length_upload += len(in_chunk)
                                if total_length > 0:
                                    transferred_percentage_upload = int(
                                        (transferred_length_upload / total_length) * 100)
                                else:
                                    transferred_percentage_upload = 0
                                if transferred_percentage_upload >= next_percentage_to_print_upload:
                                    curr_timestamp_upload = int(round(time.time() * 1000))
                                    duration_upload = curr_timestamp_upload - prev_timestamp_upload
                                    delta_length_upload = \
                                        transferred_length_upload - prev_transferred_length_upload
                                    if duration_upload > 0:
                                        rate_upload = delta_length_upload / (duration_upload / 1000)
                                    else:
                                        rate_upload = 0
                                    prev_timestamp_upload = curr_timestamp_upload
                                    prev_transferred_length_upload = transferred_length_upload
                                    print("Replicating to %s (%s) ... %d%% (%.3f MB/s)" %
                                          (target_server_name, target_server_address, transferred_percentage_upload,
                                           (rate_upload / 1000000)))
                                    next_percentage_to_print_upload = next_percentage_to_print_upload + 5

                    # import target image
                    logger.debug("Opening target image")
                    url = base_dst_api_url + alt_image_backend_url + '/' + image_filename
                    total_length = int(r_export.headers.get('content-length'))
                    # r_import = requests.post(url, auth=(username, password), data=generate_chunk())
                    streamer = StreamingIterator(total_length, generate_chunk())
                    r_import = requests.post(url, auth=(username, password), data=streamer)
                    if not r_import.status_code == 200:
                        if r_import.status_code == 403:
                            logger.fatal("Forbidden to import image on target server.")
                            logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                            raise ProxyError()
                        else:
                            logger.fatal("Unable to import image on target server.")
                            logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                            raise ProxyError()
                    else:
                        end_timestamp = int(round(time.time()))

                        print("#### image %s (%s bytes) replicated from server: %s to server: %s (in %i secs)"
                              % (image_filename, total_length, args.source_server, target_server_name,
                                 (end_timestamp - start_timestamp)))

                else:
                    logger.fatal("Could not get status of images from server %s." % target_server_name)
                    logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                    raise ProxyError()

        print("Done.")

    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
