#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
    gns3_proxy

    GNS3 Proxy Server in Python.

    based on proxy.py - HTTP Proxy Server in Python - copyright: (c) 2013-2018 by Abhinav Singh

    :copyright: (c) 2020 by Sebastian Rieger.
    :license: BSD, see LICENSE for more details.
"""

# TODO: modification of requests/responses on-the-fly, e.g., to change advertised GNS3 server version? (faking GNS3
#       client or server version seems risky as API is not necessarily backward compatible, e.g., massive changes from
#       2.1 to 2.2)
# TODO: add logging/auditing, monitoring of load etc. (current open connections)
# TODO: integrate proxy.py updates, e.g., multi processing?
# TODO: code refactoring/removing unused proxy.py code
# TODO: evaluate reimplementing gns3_proxy as a plugin for proxy.py instead of stand-alone

# TODO: show proxy errors (e.g., console_host misconfiguration) explicitly in GNS3 GUI - if possible?
# TODO: web interface for manual replication and start/stop
# TODO: reservation system? Allowing certain users to access server backends or projects in specific timeframes
# TODO: exam environment? Lock down access to certain projects and export current state?

# TODO: LDAP integration for user login? Would require SSL connection to server which was dropped for GNS3 2.x, if
#       we tackle this, also authentication for console access could be addressed (e.g., using websockify instead of
#       telnet, using Auth or Token as in VIRL)

# TODO: Remove duplicate code fragments, esp. in gns3_proxy tools

import argparse
import os
import sys
import base64
import socket
import select
import logging
import datetime
import threading
from collections import namedtuple

import configparser
import re
import time
from ipaddress import ip_address
import json

if os.name != 'nt':
    import resource

# Default arguments
DEFAULT_CONFIG_FILE = 'gns3_proxy_config.ini'
DEFAULT_LOG_LEVEL = 'INFO'

VERSION = (0, 8)
__version__ = '.'.join(map(str, VERSION[0:2]))
__description__ = 'GNS3 Proxy based on proxy.py by Abhinav Singh (https://github.com/abhinavsingh/proxy.py)'
__author__ = 'Sebastian Rieger'
__author_email__ = 'sebastian.rieger@informatik.hs-fulda.de'
__homepage__ = 'https://github.com/srieger1/gns3-proxy'
__download_url__ = '%s/archive/develop.zip' % __homepage__
__license__ = 'BSD'
# __version__ = '.'.join(map(str, VERSION[0:2]))
# __description__ = 'Lightweight HTTP, HTTPS, WebSockets Proxy Server in a single Python file'
# __author__ = 'Abhinav Singh'
# __author_email__ = 'mailsforabhinav@gmail.com'
# __homepage__ = 'https://github.com/abhinavsingh/proxy.py'
# __download_url__ = '%s/archive/master.zip' % __homepage__
# __license__ = 'BSD'

logger = logging.getLogger(__name__)

PY3 = sys.version_info[0] == 3

if PY3:  # pragma: no cover
    text_type = str
    binary_type = bytes
    from urllib import parse as urlparse


# else:  # pragma: no cover
#    text_type = unicode
#    binary_type = str
#    import urlparse


def text_(s, encoding='utf-8', errors='strict'):  # pragma: no cover
    """Utility to ensure text-like usability.

    If ``s`` is an instance of ``binary_type``, return
    ``s.decode(encoding, errors)``, otherwise return ``s``"""
    if isinstance(s, binary_type):
        return s.decode(encoding, errors)
    return s


def bytes_(s, encoding='utf-8', errors='strict'):  # pragma: no cover
    """Utility to ensure binary-like usability.

    If ``s`` is an instance of ``text_type``, return
    ``s.encode(encoding, errors)``, otherwise return ``s``"""
    if isinstance(s, text_type):
        return s.encode(encoding, errors)
    return s


version = bytes_(__version__)
CRLF, COLON, SP = b'\r\n', b':', b' '
# PROXY_AGENT_HEADER = b'Proxy-agent: proxy.py v' + version

PROXY_TUNNEL_ESTABLISHED_RESPONSE_PKT = CRLF.join([
    b'HTTP/1.1 200 Connection established',
    # PROXY_AGENT_HEADER,
    CRLF
])

BAD_GATEWAY_RESPONSE_PKT = CRLF.join([
    b'HTTP/1.1 502 Bad Gateway',
    # PROXY_AGENT_HEADER,
    b'Content-Length: 11',
    b'Connection: close',
    CRLF
]) + b'Bad Gateway'

# PROXY_AUTHENTICATION_REQUIRED_RESPONSE_PKT = CRLF.join([
#    b'HTTP/1.1 407 Proxy Authentication Required',
#    # PROXY_AGENT_HEADER,
#    b'Content-Length: 29',
#    b'Connection: close',
#    CRLF
# ]) + b'Proxy Authentication Required'

# GNS3 Unauthorized Example HTTP Response:

# HTTP/1.1 401 Unauthorized
# X-Route: /v2/version
# Connection: close
# Server: Python/3.4 GNS3/2.1.11
# WWW-Authenticate: Basic realm="GNS3 server"
# Content-Length: 0
# Content-Type: application/octet-stream
# Date: Fri, 15 Mar 2019 20:58:59 GMT

# HSFD
PROXY_AUTHENTICATION_REQUIRED_RESPONSE_PKT = CRLF.join([
    b'HTTP/1.1 401 Unauthorized',
    b'X-Route: /v2/version',
    b'Connection: close',
    b'Server: Python/3.4 GNS3/2.1.11',
    b'WWW-Authenticate: Basic realm="GNS3 server"',
    b'Content-Length: 0',
    b'Content-Type: application/octet-stream',
    # TODO: dynamic date
    b'Date: Fri, 15 Mar 2019 20:58:59 GMT',
    CRLF
])


class ChunkParser(object):
    """HTTP chunked encoding response parser."""

    states = namedtuple('ChunkParserStates', (
        'WAITING_FOR_SIZE',
        'WAITING_FOR_DATA',
        'COMPLETE'
    ))(1, 2, 3)

    def __init__(self):
        self.state = ChunkParser.states.WAITING_FOR_SIZE
        self.body = b''  # Parsed chunks
        self.chunk = b''  # Partial chunk received
        self.size = None  # Expected size of next following chunk

    def parse(self, data):
        more = True if len(data) > 0 else False
        while more:
            more, data = self.process(data)

    def process(self, data):
        if self.state == ChunkParser.states.WAITING_FOR_SIZE:
            # Consume prior chunk in buffer
            # in case chunk size without CRLF was received
            data = self.chunk + data
            self.chunk = b''
            # Extract following chunk data size
            line, data = HttpParser.split(data)
            if not line:  # CRLF not received
                self.chunk = data
                data = b''
            else:
                self.size = int(line, 16)
                self.state = ChunkParser.states.WAITING_FOR_DATA
        elif self.state == ChunkParser.states.WAITING_FOR_DATA:
            remaining = self.size - len(self.chunk)
            self.chunk += data[:remaining]
            data = data[remaining:]
            if len(self.chunk) == self.size:
                data = data[len(CRLF):]
                self.body += self.chunk
                if self.size == 0:
                    self.state = ChunkParser.states.COMPLETE
                else:
                    self.state = ChunkParser.states.WAITING_FOR_SIZE
                self.chunk = b''
                self.size = None
        return len(data) > 0, data


class HttpParser(object):
    """HTTP request/response parser."""

    states = namedtuple('HttpParserStates', (
        'INITIALIZED',
        'LINE_RCVD',
        'RCVING_HEADERS',
        'HEADERS_COMPLETE',
        'RCVING_BODY',
        'COMPLETE'))(1, 2, 3, 4, 5, 6)

    types = namedtuple('HttpParserTypes', (
        'REQUEST_PARSER',
        'RESPONSE_PARSER'
    ))(1, 2)

    def __init__(self, parser_type):
        assert parser_type in (HttpParser.types.REQUEST_PARSER, HttpParser.types.RESPONSE_PARSER)
        self.type = parser_type
        self.state = HttpParser.states.INITIALIZED

        self.raw = b''
        self.buffer = b''

        self.headers = dict()
        self.body = None

        self.method = None
        self.url = None
        self.code = None
        self.reason = None
        self.version = None

        self.chunk_parser = None

    def is_chunked_encoded_response(self):
        return self.type == HttpParser.types.RESPONSE_PARSER and \
               b'transfer-encoding' in self.headers and \
               self.headers[b'transfer-encoding'][1].lower() == b'chunked'

    def parse(self, data):
        self.raw += data
        data = self.buffer + data
        self.buffer = b''

        more = True if len(data) > 0 else False
        while more:
            more, data = self.process(data)
        self.buffer = data

    def process(self, data):
        # GNS3 Proxy
        # include self.method == b'GET', as well as DELETE and PUT, otherwise requests with empty JSON "{}"
        # will never get finished and will be killed after being inactive still being in state HEADERS_COMPLETE
        #
        # Reason is, that GNS3 uses REST calls, that allow GET to have a body, whereas proxy.py expects GET to
        # have an empty body, as common for regular HTTP
        if self.state in (HttpParser.states.HEADERS_COMPLETE,
                          HttpParser.states.RCVING_BODY,
                          HttpParser.states.COMPLETE) and \
                (self.method == b'POST' or
                 self.method == b'GET' or
                 self.method == b'PUT' or
                 self.method == b'DELETE' or
                 self.type == HttpParser.types.RESPONSE_PARSER):
            if not self.body:
                self.body = b''

            if b'content-length' in self.headers:
                self.state = HttpParser.states.RCVING_BODY
                self.body += data
                if len(self.body) >= int(self.headers[b'content-length'][1]):
                    self.state = HttpParser.states.COMPLETE
            elif self.is_chunked_encoded_response():
                if not self.chunk_parser:
                    self.chunk_parser = ChunkParser()
                self.chunk_parser.parse(data)
                if self.chunk_parser.state == ChunkParser.states.COMPLETE:
                    self.body = self.chunk_parser.body
                    self.state = HttpParser.states.COMPLETE

            return False, b''

        line, data = HttpParser.split(data)
        if line is False:
            return line, data

        if self.state == HttpParser.states.INITIALIZED:
            self.process_line(line)
        elif self.state in (HttpParser.states.LINE_RCVD, HttpParser.states.RCVING_HEADERS):
            self.process_header(line)

        # When connect request is received without a following host header
        # See `TestHttpParser.test_connect_request_without_host_header_request_parse` for details
        if self.state == HttpParser.states.LINE_RCVD and \
                self.type == HttpParser.types.REQUEST_PARSER and \
                self.method == b'CONNECT' and \
                data == CRLF:
            self.state = HttpParser.states.COMPLETE

        # When raw request has ended with \r\n\r\n and no more http headers are expected
        # See `TestHttpParser.test_request_parse_without_content_length` and
        # `TestHttpParser.test_response_parse_without_content_length` for details
        elif self.state == HttpParser.states.HEADERS_COMPLETE and \
                self.type == HttpParser.types.REQUEST_PARSER and \
                self.method != b'POST' and \
                self.raw.endswith(CRLF * 2):
            self.state = HttpParser.states.COMPLETE
        elif self.state == HttpParser.states.HEADERS_COMPLETE and \
                self.type == HttpParser.types.REQUEST_PARSER and \
                self.method == b'POST' and \
                (b'content-length' not in self.headers or
                 (b'content-length' in self.headers and
                  int(self.headers[b'content-length'][1]) == 0)) and \
                self.raw.endswith(CRLF * 2):
            self.state = HttpParser.states.COMPLETE

        return len(data) > 0, data

    def process_line(self, data):
        line = data.split(SP)
        if self.type == HttpParser.types.REQUEST_PARSER:
            self.method = line[0].upper()
            self.url = urlparse.urlsplit(line[1])
            self.version = line[2]
        else:
            self.version = line[0]
            self.code = line[1]
            self.reason = b' '.join(line[2:])
        self.state = HttpParser.states.LINE_RCVD

    def process_header(self, data):
        if len(data) == 0:
            if self.state == HttpParser.states.RCVING_HEADERS:
                self.state = HttpParser.states.HEADERS_COMPLETE
            elif self.state == HttpParser.states.LINE_RCVD:
                self.state = HttpParser.states.RCVING_HEADERS
        else:
            self.state = HttpParser.states.RCVING_HEADERS
            parts = data.split(COLON)
            key = parts[0].strip()
            value = COLON.join(parts[1:]).strip()
            self.headers[key.lower()] = (key, value)

    def build_url(self):
        if not self.url:
            return b'/None'

        url = self.url.path
        if url == b'':
            url = b'/'
        if not self.url.query == b'':
            url += b'?' + self.url.query
        if not self.url.fragment == b'':
            url += b'#' + self.url.fragment
        return url

    def build(self, del_headers=None, add_headers=None):
        req = b' '.join([self.method, self.build_url(), self.version])
        req += CRLF

        if not del_headers:
            del_headers = []
        for k in self.headers:
            if k not in del_headers:
                req += self.build_header(self.headers[k][0], self.headers[k][1]) + CRLF

        if not add_headers:
            add_headers = []
        for k in add_headers:
            req += self.build_header(k[0], k[1]) + CRLF

        req += CRLF
        if self.body:
            req += self.body

        return req

    @staticmethod
    def build_header(k, v):
        return k + b': ' + v

    @staticmethod
    def split(data):
        pos = data.find(CRLF)
        if pos == -1:
            return False, data
        line = data[:pos]
        data = data[pos + len(CRLF):]
        return line, data


class Connection(object):
    """TCP server/client connection abstraction."""

    def __init__(self, what):
        self.conn = None
        self.buffer = b''
        self.closed = False
        self.what = what  # server or client

    def send(self, data):
        # TODO: Gracefully handle BrokenPipeError exceptions
        return self.conn.send(data)

    def recv(self, bufsiz=8192):
        try:
            data = self.conn.recv(bufsiz)
            if len(data) == 0:
                logger.debug('rcvd 0 bytes from %s' % self.what)
                return None
            logger.debug('rcvd %d bytes from %s' % (len(data), self.what))
            return data
        except Exception as e:
            # if errno == errno.ECONNRESET:
            #    logger.debug('%r' % e)
            # else:
            logger.exception(
                'Exception while receiving from connection %s %r with reason %r' % (self.what, self.conn, e))
            return None

    def close(self):
        # GNS3 proxy needs a clean closing of connections, otherwise, e.g., conn will be closed before
        # HTTP response is delivered and for example ProxyAuthenticationException will not be displayed
        # self.conn.close()
        self.conn.shutdown(socket.SHUT_WR)
        time.sleep(1)
        self.conn.close()
        self.closed = True

    def buffer_size(self):
        return len(self.buffer)

    def has_buffer(self):
        return self.buffer_size() > 0

    def queue(self, data):
        self.buffer += data

    def flush(self):
        sent = self.send(self.buffer)
        self.buffer = self.buffer[sent:]
        logger.debug('flushed %d bytes to %s' % (sent, self.what))


class Server(Connection):
    """Establish connection to destination server."""

    def __init__(self, host, port):
        super(Server, self).__init__(b'server')
        self.addr = (host, int(port))

    def __del__(self):
        if self.conn:
            self.close()

    def connect(self):
        self.conn = socket.create_connection((self.addr[0], self.addr[1]))


class Client(Connection):
    """Accepted client connection."""

    def __init__(self, conn, addr):
        super(Client, self).__init__(b'client')
        self.conn = conn
        self.addr = addr


class ProxyError(Exception):
    pass


class ProxyConnectionFailed(ProxyError):

    def __init__(self, host, port, reason):
        self.host = host
        self.port = port
        self.reason = reason

    def __str__(self):
        return '<ProxyConnectionFailed - %s:%s - %s>' % (self.host, self.port, self.reason)


class ProxyAuthenticationFailed(ProxyError):
    pass


class Proxy(threading.Thread):
    """HTTP proxy implementation.

    Accepts `Client` connection object and act as a proxy between client and server.
    """

    def __init__(self, client, backend_auth_code=None, backend_port=3080, server_recvbuf_size=81920,
                 client_recvbuf_size=81920, default_server=None, config_servers=None,
                 config_users=None, config_mapping=None, config_project_filter=None, config_deny=None):
        super(Proxy, self).__init__()

        self.start_time = self._now()
        self.last_activity = self.start_time

        self.client = client
        self.client_recvbuf_size = client_recvbuf_size
        self.server = None
        self.server_recvbuf_size = server_recvbuf_size
        self.username = None

        self.backend_auth_code = backend_auth_code
        self.backend_port = backend_port
        self.default_server = default_server
        self.config_servers = config_servers
        self.config_users = config_users
        self.config_mapping = config_mapping
        self.config_project_filter = config_project_filter
        self.config_deny = config_deny

        self.request = HttpParser(HttpParser.types.REQUEST_PARSER)
        self.response = HttpParser(HttpParser.types.RESPONSE_PARSER)

    @staticmethod
    def _now():
        return datetime.datetime.utcnow()

    def _inactive_for(self):
        return (self._now() - self.last_activity).seconds

    def _is_inactive(self):
        return self._inactive_for() > 30

    def _process_request(self, data):
        # once we have connection to the server
        # we don't parse the http request packets
        # any further, instead just pipe incoming
        # data from client to server
        if self.server and not self.server.closed:
            self.server.queue(data)
            return

        # parse http request
        self.request.parse(data)

        # once http request parser has reached the state complete
        # we attempt to establish connection to destination server
        if self.request.state == HttpParser.states.COMPLETE:
            logger.debug('request parser is in state complete')

            # GNS3 Proxy Authentication
            logger.debug("Received request from client %s" % self.client.addr[0])

            # Checking authentication and authorization of user supplied in request
            if b'authorization' in self.request.headers:
                auth_request = text_(base64.b64decode(self.request.headers[b'authorization'][1][6:])).split(":")
                username = auth_request[0]
                if auth_request[1]:
                    password = auth_request[1]
                else:
                    # empty password, seems to be sent from GNS3 GUI during connection setup/server discovery
                    password = None
                logger.debug("Received Authorization request %s %s %s" % (auth_request, username, password))

                # Lookup user from request in config
                if self.config_users is not None and username in self.config_users:
                    # Check submitted password
                    if self.config_users[username] == password:
                        logger.debug("Successfully authenticated user %s" % username)
                        self.username = username
                    else:
                        logger.error("Wrong password for user %s" % username)
                        raise ProxyAuthenticationFailed()
                else:
                    logger.error("User %s not found in config" % username)
                    raise ProxyAuthenticationFailed()
            else:
                logger.error(
                    "Request did not contain an Authorization header. Please provide username and password in client.")
                raise ProxyAuthenticationFailed()

            # TODO: do not allow operations on filtered projects
            # if /v2/projects/{project_id} ... resolve id? maybe store list of allowed ids on first project list
            # retrieval, use as while list containing allowed project IDs
            # a bit of a cosmetic issue, as using Web app and GNS3 client will not show filtered projects anyway
            # only REST calls would be possible

            # evaluate denied requests
            if self.config_deny is not None and len(self.config_deny) > 0:
                for item in self.config_deny:
                    if self.config_users is not None:
                        for key in self.config_users:
                            if re.fullmatch(item["user"], key):
                                logger.debug("Deny matched user %s = %s" % (item["user"], key))
                                access_user = key
                                logger.debug("Trying to match %s as %s" % (username, access_user))
                                if username == access_user:
                                    logger.debug(
                                        "User matched mapping %s = %s, evaluating deny rule %s" % (
                                            item["user"], key, item))
                                    logger.debug(
                                        "Debug deny rule %s %s" % (
                                            text_(self.request.method), text_(self.request.url.path)))
                                    # logger.info("Method: %s %s %s" % ((re.fullmatch(item["method"],text_(self.request.method)),
                                    #   item["method"], text_(self.request.method))))
                                    # logger.info("Path: %s %s %s" % ((re.fullmatch(item["url"],text_(self.request.url.path)),
                                    #   item["url"], text_(self.request.url.path))))
                                    if (((item["method"] == "") or re.fullmatch(item["method"],
                                                                                text_(self.request.method))) and
                                            (item["url"] == "" or re.fullmatch(item["url"],
                                                                               text_(self.request.url.path))) and
                                            (item["header"] == "" or re.fullmatch(item["header"],
                                                                                  text_(self.request.headers))) and
                                            (item["body"] == "" or re.fullmatch(item["body"],
                                                                                text_(self.request.body)))):
                                        logger.info("Request denied due to matching rule %s", item)
                                        raise ProxyAuthenticationFailed()
                    else:
                        logger.info("Cannot evaluate deny rules. No users found in config.")
                        raise ProxyAuthenticationFailed()

            # CONNECT not used by GNS3?
            # if self.request.method == b'CONNECT':
            #    host, port = self.request.url.path.split(COLON)
            # elif self.request.url:
            #    host, port = self.request.url.hostname, self.request.url.port if self.request.url.port else 80
            # else:
            #    raise Exception('Invalid request\n%s' % self.request.raw)

            # Redirect request based on client and user to appropriate backend server, according to mapping from config
            # or default backend server
            backend_server = None

            # Try to find match for user in config
            if self.config_mapping is not None and len(self.config_mapping) > 0:
                for item in self.config_mapping:
                    if self.config_users is not None:
                        for key in self.config_users:
                            if re.fullmatch(item["match"], key):
                                logger.debug("User mapping matched %s = %s" % (item["match"], key))
                                access_user = key
                                logger.debug("Trying to match %s as %s" % (username, access_user))
                                if username == access_user:
                                    logger.debug("User matched mapping %s = %s, choosing server %s" % (
                                        item["match"], key, item["server"]))
                                    if self.config_servers is not None and item["server"] in self.config_servers:
                                        backend_server = self.config_servers[item["server"]]
                                    else:
                                        logger.fatal("Mapped server %s not found in config." % item["server"])
                                        raise ProxyError()
                                    break
                    else:
                        logger.info("Cannot evaluate mapping rules. No users found in config.")
                        raise ProxyAuthenticationFailed()

            # if no server was chosen by mapping, try using default, otherwise raise exception
            if backend_server is None:
                if self.default_server is not None:
                    # if a default server is set in config, choose this one by default
                    if self.config_servers is not None and self.default_server in self.config_servers:
                        backend_server = self.config_servers[self.default_server]
                        logger.debug("Redirecting client %s to default backend server %s:%s" % (
                            self.client.addr[0], backend_server, self.backend_port))
                    else:
                        try:
                            backend_server = str(ip_address(self.default_server))
                            logger.debug("Trying to redirecting client %s to default backend server IP %s:%s" % (
                                self.client.addr[0], backend_server, self.backend_port))
                        except ValueError:
                            logger.fatal(
                                "Default server %s is neither an entry in server config nor a valid IP address"
                                % self.default_server)
                            raise ProxyError()
                else:
                    logger.error("Cannot find appropriate server using mapping and no default server defined in "
                                 "config.")
                    raise ProxyAuthenticationFailed()

            self.server = Server(backend_server, self.backend_port)
            try:
                logger.debug('connecting to server %s:%s' % (backend_server, self.backend_port))
                self.server.connect()
                logger.debug('connected to server %s:%s' % (backend_server, self.backend_port))
            except Exception as e:  # TimeoutError, socket.gaierror
                self.server.closed = True
                raise ProxyConnectionFailed(backend_server, self.backend_port, repr(e))

            # for http connect methods (https requests)
            # queue appropriate response for client
            # notifying about established connection
            if self.request.method == b'CONNECT':
                self.client.queue(PROXY_TUNNEL_ESTABLISHED_RESPONSE_PKT)
            # for usual http requests, re-build request packet
            # and queue for the server with appropriate headers
            else:
                self.server.queue(self.request.build(
                    # GNS3 Proxy REMOVED
                    #
                    # del_headers=[b'proxy-authorization', b'proxy-connection', b'connection', b'keep-alive'],
                    # add_headers=[(b'Via', b'1.1 proxy.py v%s' % version), (b'Connection', b'Close')]

                    del_headers=[b'authorization'],
                    add_headers=[(b'Authorization', self.backend_auth_code)]
                ))

    def _process_response(self, data):
        # parse incoming response packet
        # only for non-https requests
        if not self.request.method == b'CONNECT':

            self.response.parse(data)

            # filter project list
            if self.config_project_filter is not None:
                if b'x-route' in self.response.headers and self.response.headers[b'x-route'][1].lower() == \
                        b'/v2/projects':
                    logger.debug("Filtering project library in response")
                    user_matched = False
                    user_project_filters = list()
                    for project_filter in self.config_project_filter:
                        if re.fullmatch(project_filter["match"], self.username):
                            logger.debug("Project filter %s matched for user %s" % (project_filter["match"],
                                                                                    self.username))
                            user_matched = True
                            user_project_filters.append(project_filter)

                    if user_matched:
                        header_block = data[:data.find(b'\r\n\r\n')]
                        body = data[data.find(b'\r\n\r\n'):]

                        # make sure that body is complete, otherwise we cannot load and decode contained JSON
                        for header in header_block.split(CRLF):
                            if text_(header).startswith("Content-Length:"):
                                content_length = int(text_(header).split(":")[1])
                        while len(body) - 4 < content_length:
                            logger.debug("Body is not complete (len: %d of content-length: %d), cannot decode JSON, "
                                        "trying to receive further content" % (len(body) - 4, content_length))
                            data += self.server.recv(self.server_recvbuf_size)
                            body = data[data.find(b'\r\n\r\n'):]
                            logger.debug("(len: %d of content-length: %d)" % (len(body) - 4, content_length))

                        try:
                            projects = json.loads(body)
                            projects_filtered = list()

                            for user_project_filter in user_project_filters:
                                for project in projects:
                                    if re.fullmatch(user_project_filter["filter"], project["name"]):
                                        logger.debug("Allowing project %s for user %s" % (project, self.username))
                                        if project not in projects_filtered:
                                            projects_filtered.append(project)

                            logger.info("Filtered project library for user %s from %d to %d entries.",
                                        self.username, len(projects), len(projects_filtered))

                            body = json.dumps(projects_filtered)
                            header_block_out = b''
                            for header in header_block.split(CRLF):
                                if text_(header).startswith("Content-Length:"):
                                    header_block_out += bytes_("Content-Length: " + str(len(body))) + CRLF
                                else:
                                    header_block_out += header + CRLF

                            new_data = bytes_(header_block_out) + b'\r\n' + bytes_(body)
                            data = new_data
                        except json.decoder.JSONDecodeError as jde:
                            logger.error("JSONDecodeError during project filtering. %s %s", body, jde)

                # if b'x-route' in self.response.headers:
                #     if self.response.headers[b'x-route'][1].lower() == b'/v2/projects/{project_id}/open':
                #         logger.debug("x-route: %s", self.response.headers[b'x-route'][1].lower())

            # check console_host config in project nodes, if value is "0.0.0.0" backend is likely not setup correctly
            # to work with the proxy (i.e., consoles not being accessible)
            if b'x-route' in self.response.headers:
                if self.response.headers[b'x-route'][1].lower() == b'/v2/projects/{project_id}/nodes':
                    logger.debug("Checking console_host in response for %s", self.response.headers[b'x-route'])
                    if data.find(b"\"console_host\": \"0.0.0.0\",") != -1:
                        logger.fatal("Backend %s is likely to be misconfigured! In gns3_server.conf host needs to be"
                                     "changed to the primary IP address also used in the backend config. Seems to be"
                                     "host = 0.0.0.0. Console connections to nodes on this backend will not work"
                                     "when accessed through the proxy! See also gns3_proxy setup documentation."
                                     % str(self.server.addr))
                        raise ProxyError()
                    # data = data.replace(b"\"console_host\": \"0.0.0.0\",", b"\"console_host\": \"192.168.76.205\",")
                    # data = data.replace(b"\"console_host\": \"0.0.0.0\",", b"\"console_host\": \"0.0.0.0\",")
                    # logger.info("%s", data)

            # demo to intercept specific response
            # if b'x-route' in self.response.headers:
            #    if self.response.headers[b'x-route'][1].lower() == b'/v2/settings':
            #        logger.info("%s", self.response.headers[b'x-route'])

            # GNS3 Proxy Example to rewrite response content
            #
            # allow rewrite of console_host, does not work, not allowed to change console host in config, needs to be
            # set in gns3_server.conf
            #
            # if b'x-route' in self.response.headers:
            #    if self.response.headers[b'x-route'][1].lower() == b'/v2/projects/{project_id}/nodes':
            #        logger.info("%s", self.response.headers[b'x-route'])
            #        #data = data.replace(b"\"console_host\": \"0.0.0.0\",", b"\"console_host\": \"192.168.76.205\",")
            #        #data = data.replace(b"\"console_host\": \"0.0.0.0\",", b"\"console_host\": \"0.0.0.0\",")
            #        #logger.info("%s", data)

        # queue data for client
        self.client.queue(data)

    def _access_log(self):
        host, port = self.server.addr if self.server else (None, None)
        if self.request.method == b'CONNECT':
            logger.info(
                '%s:%s - %s %s:%s' % (self.client.addr[0], self.client.addr[1], self.request.method, host, port))
        elif self.request.method:
            logger.info('%s:%s (%s) - %s %s:%s%s - %s %s - %s bytes (%s threads)' % (
                self.client.addr[0], self.client.addr[1], self.username, self.request.method, host, port,
                self.request.build_url(), self.response.code, self.response.reason, len(self.response.raw),
                threading.active_count()))

    def _get_waitable_lists(self):
        rlist, wlist, xlist = [self.client.conn], [], []
        if self.client.has_buffer():
            wlist.append(self.client.conn)
        if self.server and not self.server.closed:
            rlist.append(self.server.conn)
        if self.server and not self.server.closed and self.server.has_buffer():
            wlist.append(self.server.conn)
        return rlist, wlist, xlist

    def _process_wlist(self, w):
        if self.client.conn in w:
            logger.debug('client is ready for writes, flushing client buffer')
            self.client.flush()

        if self.server and not self.server.closed and self.server.conn in w:
            logger.debug('server is ready for writes, flushing server buffer')
            self.server.flush()

    def _process_rlist(self, r):
        """Returns True if connection to client must be closed."""
        if self.client.conn in r:
            logger.debug('client is ready for reads, reading')
            data = self.client.recv(self.client_recvbuf_size)
            self.last_activity = self._now()

            if not data:
                logger.debug('client closed connection, breaking')
                return True

            try:
                self._process_request(data)
            except (ProxyAuthenticationFailed, ProxyConnectionFailed) as e:
                logger.exception(e)
                self.client.queue(Proxy._get_response_pkt_by_exception(e))
                self.client.flush()
                return True

        if self.server and not self.server.closed and self.server.conn in r:
            logger.debug('server is ready for reads, reading')
            data = self.server.recv(self.server_recvbuf_size)
            self.last_activity = self._now()

            if not data:
                logger.debug('server closed connection')
                self.server.close()
            else:
                self._process_response(data)

        return False

    def _process(self):
        while True:
            rlist, wlist, xlist = self._get_waitable_lists()
            r, w, x = select.select(rlist, wlist, xlist, 1)

            self._process_wlist(w)
            if self._process_rlist(r):
                break

            if self.client.buffer_size() == 0:
                if self.response.state == HttpParser.states.COMPLETE:
                    logger.debug('client buffer is empty and response state is complete, breaking')
                    break

                if self._is_inactive():
                    logger.info('client buffer is empty and maximum inactivity has reached, breaking')
                    break

    @staticmethod
    def _get_response_pkt_by_exception(e):
        if e.__class__.__name__ == 'ProxyAuthenticationFailed':
            return PROXY_AUTHENTICATION_REQUIRED_RESPONSE_PKT
        if e.__class__.__name__ == 'ProxyConnectionFailed':
            return BAD_GATEWAY_RESPONSE_PKT

    def run(self):
        logger.debug('Proxying connection %r' % self.client.conn)
        try:
            self._process()
        except KeyboardInterrupt:
            pass
        except Exception as e:
            logger.exception('Exception while handling connection %r with reason %r' % (self.client.conn, e))
        finally:
            logger.debug(
                'closing client connection with pending client buffer size %d bytes' % self.client.buffer_size())
            self.client.close()
            if self.server:
                logger.debug(
                    'closed client connection with pending server buffer size %d bytes' % self.server.buffer_size())
            self._access_log()
            logger.debug('Closing proxy for connection %r at address %r' % (self.client.conn, self.client.addr))


class TCP(object):
    """TCP server implementation.

    Subclass MUST implement `handle` method. It accepts an instance of accepted `Client` connection.
    """

    def __init__(self, hostname='127.0.0.1', port=13080, backlog=100):
        self.hostname = hostname
        self.port = port
        self.backlog = backlog
        self.socket = None

    def handle(self, client):
        raise NotImplementedError()

    def run(self):
        try:
            logger.info('Starting server on port %d' % self.port)
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.hostname, self.port))
            self.socket.listen(self.backlog)
            while True:
                conn, addr = self.socket.accept()
                client = Client(conn, addr)
                self.handle(client)
        except Exception as e:
            logger.exception('Exception while running the server %r' % e)
        finally:
            logger.info('Closing server socket')
            self.socket.close()


class HTTP(TCP):
    """HTTP proxy server implementation.

    Spawns new process to proxy accepted client connection.
    """

    def __init__(self, hostname='127.0.0.1', port=13080, backlog=100,
                 backend_auth_code=None, backend_port=3080, server_recvbuf_size=81920, client_recvbuf_size=81920,
                 default_server=None, config_servers=None, config_users=None, config_mapping=None,
                 config_project_filter=None, config_deny=None):
        super(HTTP, self).__init__(hostname, port, backlog)
        self.client_recvbuf_size = client_recvbuf_size
        self.server_recvbuf_size = server_recvbuf_size

        self.backend_auth_code = backend_auth_code
        self.backend_port = backend_port
        self.default_server = default_server
        self.config_servers = config_servers
        self.config_users = config_users
        self.config_mapping = config_mapping
        self.config_project_filter = config_project_filter
        self.config_deny = config_deny

    def handle(self, client):
        proxy = Proxy(client,
                      backend_auth_code=self.backend_auth_code,
                      backend_port=self.backend_port,
                      server_recvbuf_size=self.server_recvbuf_size,
                      client_recvbuf_size=self.client_recvbuf_size,
                      default_server=self.default_server,
                      config_servers=self.config_servers,
                      config_users=self.config_users,
                      config_mapping=self.config_mapping,
                      config_project_filter=self.config_project_filter,
                      config_deny=self.config_deny)
        proxy.daemon = True
        proxy.start()


def set_open_file_limit(soft_limit):
    """Configure open file description soft limit on supported OS."""
    if os.name != 'nt':  # resource module not available on Windows OS
        curr_soft_limit, curr_hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
        if curr_soft_limit < soft_limit < curr_hard_limit:
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft_limit, curr_hard_limit))
            logger.info('Open file descriptor soft limit set to %d' % soft_limit)


def parse_args(args):
    parser = argparse.ArgumentParser(
        description='gns3_proxy.py v%s.' % __version__,
        epilog='gns3_proxy not working? Report at: %s/issues/new' % __homepage__
    )
    # Argument names are ordered alphabetically.
    parser.add_argument('--config-file', type=str, default=DEFAULT_CONFIG_FILE,
                        help='Location of the gns3_proxy config file. Default: gns3_proxy_config.ini.')
    parser.add_argument('--log-level', type=str, default=DEFAULT_LOG_LEVEL,
                        help='Valid options: DEBUG, INFO (default), WARNING, ERROR, CRITICAL. '
                             'Both upper and lowercase values are allowed.'
                             'You may also simply use the leading character e.g. --log-level d')

    return parser.parse_args(args)


def main():
    # parse arguments
    args = parse_args(sys.argv[1:])

    logging.basicConfig(level=getattr(logging, args.log_level),
                        format='%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s')

    # parse config file gns3_proxy_config.ini
    config = configparser.ConfigParser()
    config.read_file(open(args.config_file))
    config.read(args.config_file)

    # get hostname
    #
    # description: Hostname (and hence IP address or interface) the GNS3 proxy listens on
    # default: 127.0.0.1
    if config.get('proxy', 'hostname'):
        hostname = config.get('proxy', 'hostname')
    else:
        hostname = "127.0.0.1"

    # get port
    #
    # description: TCP port the GNS3 proxy listens on
    # default: 13080
    if config.get('proxy', 'port'):
        port = config.getint('proxy', 'port')
    else:
        port = 13080

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

    backend_auth_code = b'Basic ' + base64.b64encode(bytes_(backend_user + ":" + backend_password))

    # get backend_port
    #
    # description: TCP port to use to access backend GNS3 server
    # default: 3080
    if config.get('proxy', 'backend_port'):
        backend_port = config.getint('proxy', 'backend_port')
    else:
        backend_port = 3080

    # get default_server
    #
    # description: Default GNS3 server to use as a fallback backend if none of the mappings matched
    # default: None
    if config.get('proxy', 'default_server'):
        default_server = config.get('proxy', 'default_server')
    else:
        default_server = None

    # get backlog
    #
    # description: Maximum number of pending connections to proxy server
    # default: 100
    if config.get('proxy', 'backlog'):
        backlog = config.getint('proxy', 'backlog')
    else:
        backlog = 100

    # get server-recvbuf-size config
    #
    # description: Maximum amount of data received from the server in a single
    #     recv() operation. Bump this value for faster uploads at the expense of
    #     increased RAM.
    # default: 1024
    if config.get('proxy', 'server-recvbuf-size'):
        server_recvbuf_size = config.getint('proxy', 'server-recvbuf-size')
    else:
        server_recvbuf_size = 81920

    # get client-recvbuf-size config
    #
    # description: Maximum amount of data received from the client in a single
    #     recv() operation. Bump this value for faster uploads at the expense of
    #     increased RAM.
    # default: 1024
    if config.get('proxy', 'client-recvbuf-size'):
        client_recvbuf_size = config.getint('proxy', 'client-recvbuf-size')
    else:
        client_recvbuf_size = 81920

    # get open-file-limit config
    #
    # description: Maximum number of files (TCP connections) that gns3-proxy can open concurrently.
    # default: 8192
    if config.get('proxy', 'open-file-limit'):
        open_file_limit = config.getint('proxy', 'open-file-limit')
    else:
        open_file_limit = 1024

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

    # read users from config
    if config.items('users'):
        config_users = dict()
        user_items = config.items('users')
        for key, value in user_items:
            config_users[key] = value
    else:
        config_users = None

    # read mapping from config
    if config.items('mapping'):
        config_mapping = list()
        mapping_items = config.items('mapping')
        for key, value in mapping_items:
            # mapping line must be in format "<user match>":"<server>", e.g.:
            #   "user(.*)":"gns3-server-1"
            #   "user2":"gns3-2"

            # temporarily replace escaped quotation marks to preserve them
            # tempvalue = str(value).replace("\"","'")
            if not re.fullmatch("^\"([^\"]*)\":\"([^\"]*)\"$", value):
                logger.fatal(
                    "mapping config not valid. Line %s is not in format \"<user match>\":\"<server>\"" % value)
                raise ProxyError()
            # cut off quotation mark at beginning and end of line and split components
            mapping_value = str(value[1:-1]).split("\":\"")
            logger.debug("config mapping value %s" % mapping_value)
            mapping_match = mapping_value[0]
            mapping_server = mapping_value[1]
            if mapping_server in config_servers:
                config_mapping.append({"match": mapping_match, "server": mapping_server})
            else:
                logger.fatal("mapping config not valid. Server %s in line %s is not defined in servers." % (
                    mapping_server, value))
                raise ProxyError()
    else:
        config_mapping = None

    # read project filter from config
    if config.items('project-filter'):
        config_project_filter = list()
        project_filter_items = config.items('project-filter')
        for key, value in project_filter_items:
            # project-filter line must be in format "<user match>":"<filter>", e.g.:
            #   "user(.*)":"(.*)Group1(.*)"
            #   "user2":"Test Lab"

            # temporarily replace escaped quotation marks to preserve them
            # tempvalue = str(value).replace("\"","'")
            if not re.fullmatch("^\"([^\"]*)\":\"([^\"]*)\"$", value):
                logger.fatal(
                    "project-filter config not valid. Line %s is not in format \"<user match>\":\"<filter>\"" % value)
                raise ProxyError()
            # cut off quotation mark at beginning and end of line and split components
            project_filter_value = str(value[1:-1]).split("\":\"")
            logger.debug("config project-filter value %s" % project_filter_value)
            project_filter_match = project_filter_value[0]
            project_filter_filter = project_filter_value[1]
            config_project_filter.append({"match": project_filter_match, "filter": project_filter_filter})
    else:
        config_project_filter = None

    # read deny from config
    if config.items('deny'):
        config_deny = list()
        deny_items = config.items('deny')
        for key, value in deny_items:
            # deny line must be in format "<user pattern>":"<http method pattern>":"<http url pattern>":
            #   "http header pattern":"http body pattern", e.g.:
            #
            #   "user(.*)":"POST":"/nodes$":"":""
            #   "user(.*)":"PUT":"":"":"xyz"

            # temporarily replace escaped quotation marks to perserve them 
            # tempvalue = str(value).replace("\"","'")
            if not re.fullmatch("^\"([^\"]*)\":\"([^\"]*)\":\"([^\"]*)\":\"([^\"]*)\":\"([^\"]*)\"$", value):
                logger.fatal(
                    "deny config not valid. Line %s is not in format \"<user pattern>\":\"<http method pattern>\":"
                    "\"<http url pattern>\":\"http header pattern\":\"http body pattern\"" % value)
                raise ProxyError()
            # cut off quotation mark at beginning and end of line and split components
            deny_value = str(value[1:-1]).split("\":\"")
            logger.debug("config deny value %s" % deny_value)
            deny_user_match = deny_value[0]
            deny_http_method_match = deny_value[1]
            deny_http_url_match = deny_value[2]
            deny_http_header_match = deny_value[3]
            deny_http_body_match = deny_value[4]
            config_deny.append({"user": deny_user_match, "method": deny_http_method_match, "url": deny_http_url_match,
                                "header": deny_http_header_match, "body": deny_http_body_match})
    else:
        config_deny = None

    logger.debug("Config hostname: %s" % hostname)
    logger.debug("Config port: %s" % port)
    logger.debug("Config backend_user: %s" % backend_user)
    logger.debug("Config backend_password: %s" % backend_password)
    logger.debug("Config backend_port: %s" % backend_port)
    logger.debug("Config default_server: %s" % default_server)
    logger.debug("Config backlog: %s" % backlog)
    logger.debug("Config server-recvbuf-size: %s" % server_recvbuf_size)
    logger.debug("Config client-recvbuf-size: %s" % client_recvbuf_size)
    logger.debug("Config open-file-limit: %s" % open_file_limit)

    logger.debug("Config servers: %s" % config_servers)
    logger.debug("Config users: %s" % config_users)
    logger.debug("Config mapping: %s" % config_mapping)
    logger.debug("Config project-filter: %s" % config_project_filter)
    logger.debug("Config deny: %s" % config_deny)

    try:
        set_open_file_limit(open_file_limit)

        proxy = HTTP(hostname=hostname,
                     port=port,
                     backlog=backlog,
                     backend_auth_code=backend_auth_code,
                     backend_port=backend_port,
                     server_recvbuf_size=server_recvbuf_size,
                     client_recvbuf_size=client_recvbuf_size,
                     default_server=default_server,
                     config_servers=config_servers,
                     config_users=config_users,
                     config_mapping=config_mapping,
                     config_project_filter=config_project_filter,
                     config_deny=config_deny)
        proxy.run()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
