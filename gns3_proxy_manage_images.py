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

import requests

VERSION = (0, 4)
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
DEFAULT_SHOW_ACTION = False
DEFAULT_FORCE = False


class ProxyError(Exception):
    pass


def parse_args(args):
    parser = argparse.ArgumentParser(
        description='gns3_proxy_manage_images.py v%s Manage images on GNS3 proxy backends.' % __version__,
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

    parser.add_argument('--image-type', type=str, required=True, choices=['qemu', 'dynamips', 'iou', 'docker'],
                        help='Type of the images to be managed.'
                             'GNS3 currently uses different API for each image type.')
    # docker is also possible /v2/compute/docker/images, but only to show pulled container
    # images. They are automatically pulled if not available anyway

    parser.add_argument('--image-filename', type=str, required=True,
                        help='Name of the images to be managed.'
                             'Can be specified as a regular expression to match multiple images.')

    parser.add_argument('--buffer', type=int, required=False, default=8192,
                        help='Number of bytes to use for buffering download and upload of images.')

    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument('--export-to-dir', type=str,
                              help='Export image to directory.')
    action_group.add_argument('--import-from-file', type=str,
                              help='Import image from file.')
    # Deletion of images is currently not supported by GNS3, images of deleted templates have to be manually deleted
    #     on the GNS3 backends
    #
    # action_group.add_argument('--delete', action='store_true', default=DEFAULT_DELETE_ACTION,
    #                          help='Delete images.')
    action_group.add_argument('--show', action='store_true', default=DEFAULT_SHOW_ACTION,
                              help='Show images and their status.')

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

        print("#### Handling image type %s" % args.image_type)

        # Compute Image Backend
        image_backend_url = '/compute/' + args.image_type + '/images'

        # Alternate location for image access, used for upload by the GNS3 client, but download (GET) throws an error
        alt_image_backend_url = '/computes/local/' + args.image_type + '/images'

        # Try to find match for target server in config
        if len(config_servers) > 0:
            base_dst_api_url = None
            for server in config_servers:
                if re.fullmatch(args.target_server, server):
                    logger.debug("Target server found: %s (%s) using provided match: %s" % (server,
                                                                                            config_servers[server],
                                                                                            args.target_server))
                    # build target server API URL
                    base_dst_api_url = "http://" + config_servers[server] + ":" + str(backend_port) + "/v2"

                    if args.show:
                        print("#### Showing images on server: %s" % server)

                        logger.debug("Getting status of images...")
                        url = base_dst_api_url + image_backend_url
                        r = requests.get(url, auth=(username, password))
                        if r.status_code == 200:
                            image_results = json.loads(r.text)
                            for image in image_results:
                                if args.image_type == 'docker':
                                    image_name = image['image']
                                else:
                                    image_name = image['filename']

                                if re.fullmatch(args.image_filename, image_name):
                                    print("#### Server: %s, Image: %s"
                                          % (server, image))
                        else:
                            logger.fatal("Could not get status of images from.")
                            logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                            raise ProxyError()

                    if args.export_to_dir:
                        print("#### Exporting image %s on server: %s" % (args.image_filename, server))

                        logger.debug("Getting images from target server...")
                        url = base_dst_api_url + image_backend_url
                        r = requests.get(url, auth=(username, password))
                        if r.status_code == 200:
                            image_results = json.loads(r.text)
                            for image in image_results:
                                if args.image_type == 'docker':
                                    logger.fatal("Export of image type docker currently not supported. Docker images"
                                                 "will be pulled automatically on first use on GNS3 server backend.")
                                    raise ProxyError()
                                else:
                                    image_name = image['filename']

                                if re.fullmatch(args.image_filename, image_name):
                                    logger.debug("Found image: %s on server %s"
                                                 % (image_name, server))

                                    filename = str(server) + "_" + time.strftime("%Y%m%d-%H%M%S") + image_name
                                    logger.debug("Downloading image %s to file %s " % (image_name, filename))
                                    url = base_dst_api_url + image_backend_url + '/' + image_name
                                    r = requests.get(url, auth=(username, password), stream=True)
                                    total_length = int(r.headers.get('content-length'))
                                    transferred_length = 0
                                    prev_transferred_length = 0
                                    next_percentage_to_print = 0
                                    prev_timestamp = int(round(time.time() * 1000))
                                    with open(os.path.join(args.export_to_dir, filename), 'wb', args.buffer) \
                                            as outfile:
                                        for chunk in r.iter_content(chunk_size=args.buffer):
                                            if chunk:
                                                outfile.write(chunk)
                                                transferred_length += len(chunk)
                                                if total_length > 0:
                                                    transferred_percentage = int(
                                                        (transferred_length / total_length) * 100)
                                                else:
                                                    transferred_percentage = 0
                                                if transferred_percentage >= next_percentage_to_print:
                                                    curr_timestamp = int(round(time.time() * 1000))
                                                    duration = curr_timestamp - prev_timestamp
                                                    delta_length = transferred_length - prev_transferred_length
                                                    if duration > 0:
                                                        rate = delta_length / (duration / 1000)
                                                    else:
                                                        rate = 0
                                                    prev_timestamp = curr_timestamp
                                                    prev_transferred_length = transferred_length

                                                    print("Downloading %s (%.3f MB) ... %d%% (%.3f MB/s)" %
                                                          (image_name, total_length/(1 << 20),
                                                           transferred_percentage, (rate/(1 << 20))))

                                                    next_percentage_to_print = next_percentage_to_print + 5

                                    print("#### Exported image %s from server: %s to file: "
                                          % (image_name, config_servers[server]),
                                          os.path.join(args.export_to_dir, filename))

                        else:
                            logger.fatal("Could not get status of images from server %s." % config_servers[server])
                            logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                            raise ProxyError()

                    if args.import_from_file:
                        print("#### Importing image %s on server: %s as image name: %s" %
                              (args.import_from_file, server, args.image_filename))

                        logger.debug("Checking if target image exists...")
                        url = base_dst_api_url + image_backend_url
                        r = requests.get(url, auth=(username, password))
                        if r.status_code == 200:
                            image_exists = False
                            image_to_delete = ''
                            image_results = json.loads(r.text)
                            for image in image_results:
                                if args.image_type == 'docker':
                                    logger.fatal("Export of image type docker currently not supported. Docker images"
                                                 "will be pulled automatically on first use on GNS3 server backend.")
                                    raise ProxyError()
                                else:
                                    image_name = image['filename']

                                if re.fullmatch(args.image_filename, image_name):
                                    logger.debug("image: %s already exists on server %s"
                                                 % (image_name, server))
                                    if image_exists:
                                        logger.fatal(
                                            "Multiple images matched %s on server %s. "
                                            "Import can only be used for single image." % (
                                                args.image_filename, config_servers[
                                                    server]))
                                        raise ProxyError()
                                    else:
                                        image_exists = True
                                        image_to_delete = image_name

                            if image_exists:
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
                                        % (args.image_filename, image_to_delete, server))
                                else:
                                    logger.fatal(
                                        "image: %s (%s) already exists on server %s. Use --force to overwrite it"
                                        " during import."
                                        % (args.image_filename, image_to_delete, server))
                                    raise ProxyError()

                            logger.debug("Importing image")
                            # import image
                            url = base_dst_api_url + alt_image_backend_url + '/' + args.image_filename
                            # files = {'file': open(args.import_from_file, 'rb', args.buffer)}
                            # r = requests.post(url, files=files, auth=(username, password))
                            total_length = os.stat(args.import_from_file).st_size
                            with open(args.import_from_file, 'rb', args.buffer) as infile:
                                # r = requests.post(url, auth=(username, password), data=f)

                                def generate_chunk():
                                    transferred_length_upload = 0
                                    prev_transferred_length_upload = 0
                                    next_percentage_to_print_upload = 0
                                    prev_timestamp_upload = int(round(time.time() * 1000))
                                    while True:
                                        in_chunk = infile.read(args.buffer)
                                        if not in_chunk:
                                            break
                                        yield in_chunk
                                        transferred_length_upload += len(in_chunk)
                                        if total_length > 0:
                                            transferred_percentage_upload = \
                                                int((transferred_length_upload / total_length) * 100)
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

                                            print("Uploading %s (%.3f MB) ... %d%% (%.3f MB/s)" %
                                                  (args.import_from_file, total_length / (1 << 20),
                                                   transferred_percentage_upload, (rate_upload / (1 << 20))))

                                            next_percentage_to_print_upload = next_percentage_to_print_upload + 5

                                r = requests.post(url, auth=(username, password), data=generate_chunk())
                                if not r.status_code == 200:
                                    if r.status_code == 403:
                                        logger.fatal("Forbidden to import image on target server.")
                                        logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                                        raise ProxyError()
                                    else:
                                        logger.fatal("Unable to import image on target server.")
                                        logger.debug("Status code: " + str(r.status_code) + " Text:" + r.text)
                                        raise ProxyError()
                                else:
                                    print("#### image %s imported from file: %s on server: %s as name: %s"
                                          % (args.import_from_file, args.import_from_file, server, args.image_filename))
                        else:
                            logger.fatal("Could not get status of images from server %s." % config_servers[server])
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
