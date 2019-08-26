gns3-proxy
==========

Proof-of-concept for a Proxy Server for GNS3. The proxy is configured as a 
regular remote server in the GNS3-GUI, as the GNS3-GUI client does not yet 
support proxies [gns3-gui issue #2696](https://github.com/GNS3/gns3-gui/issues/2696). Basic idea 
is to allow the use of central GNS3 server backends for classroom / lab setups,
as used, e.g., in the [Network Laboratory of Fulda University of Applied 
Sciences](https://www.hs-fulda.de/en/studies/departments/applied-computer-science/about-us/laboratories/netlab/). Students can connect to the proxy and requests will be authenticated,
filtered and forwarded to appropriate backend servers. Proxy authentication
also circumvents the current lack of multi-user support in GNS3. Without the
proxy, due to the single user limitation (see "MULTIPLE USERS ENVIRONMENT"
in [GNS3 Security](https://docs.gns3.com/1ON9JBXSeR7Nt2-Qum2o3ZX0GU86BZwlmNSUgvmqNWGY/index.html)),
users will have to use the same admin credentials for GNS3 to access the 
backend. Also, requests cannot be filtered and authorized (e.g., to deny
deletion/creation of projects etc.). As GNS3 does not support proxies, several
tweaks were necessary to the forked proxy.py project to allow transparent
REST and WebSocket passthrough.

![alt text](https://travis-ci.org/srieger1/gns3-proxy.svg?branch=develop "Build Status")

Features
--------

Inherited from proxy.py:
- Distributed as a single file module
- No external dependency other than standard Python library
- Support for `http`, `https` and `websockets` request proxy
- Optimized for large file uploads and downloads
- IPv4 and IPv6 support

Changes/enhancements to proxy.py:
- Redirect requests to backend servers (fixed proxying independent from request URL)
- Definition of users (username and password used in GNS3-GUI) for authentication and authorization at the proxy, proxy replaces credentials for backend servers
- Selection (mapping) of GNS3 backend server and possibility of load-balancing based on username (using regexp)
- Filtering of denied requests to server backends (based on username, REST/HTTP method/URL path/headers/body (using regexp)
- Configuration file to allow basic proxy configuration as well as GNS3 backend server, users, mappings and request filters
- Support for REST calls (GET requests with body etc., not handled by proxy.py)
- Fixes and tweaks to allow the connection to GNS3 backends, especially keeping connections alive and leaving HTTP headers to support direct passthrough of WebSocket connections
- Basic access logging/status monitoring support

Further utilities provided to use the proxy:
- [gns3_proxy_manage_projects.py](https://github.com/srieger1/gns3-proxy/blob/develop/gns3_proxy_manage_projects.py) allows management of projects on backend servers, e.g., bulk import, export,
  start, stop, delete projects on all or certain backend servers based on regexp.
- [gns3_proxy_replicate_projects.py](https://github.com/srieger1/gns3-proxy/blob/develop/gns3_proxy_replicate_projects.py) supports replication of projects across backend servers.

gns3_proxy_manage_project.py and gns3_proxy_replicate_projects.py be combined with [cron entry](https://github.com/srieger1/gns3-proxy/blob/develop/gns3_proxy_crontab) to run tasks periodically.   

Concept
-------

In our Network Laboratory we use several network emulators (besides GNS3 esp., mininet, VIRL and EVE-NG) and simulators
for courses and lab sessions as well as individual research or students' projects. As GNS3 is focusing on single user
installations, several changes were necessary to provide lab session in class as well as to students working from
at home. The following figure describes our setup:

![gns3 proxy setup figure including external clients, backend servers and the proxy in the middle as well as its functions](https://raw.githubusercontent.com/srieger1/gns3-proxy/develop/gns3-proxy-concept.png "GNS3 proxy setup in the NetLab of Fulda University of Applied Sciences")

Using gns3-proxy, we can use separate credentials for users accessing the proxy without needing to share the single
admin user provided by the standard gns3 server. However, no modifications are necessary to the standard GNS3 server
used in our backends and for the GNS3 client GUI. Users defined in the proxy, e.g., a group of students working
together in a group or on individual projects from at home, will be mapped to an individual backend server allowing
load balancing and failover, since GNS3 compared to other network emulation environments does not offer a cluster setup
to spread running projects and contained resources. The proxy also allows to filter and hence deny requests that
contain modifications to projects. Prepared projects are periodically synced to all server backends using cron and the
replication utility [gns3_proxy_replicate_projects.py](https://github.com/srieger1/gns3-proxy/blob/develop/gns3_proxy_replicate_projects.py).

Installation
------------

You can clone this repository or simply copy gns3_proxy.py and gns3_proxy_config.ini to a host that has Python >=3.4
installed. 

Even easier is the installation using a Docker container. Simply install and run the latest version of the [gns3-proxy
container image](https://cloud.docker.com/u/flex/repository/docker/flex/gns3-proxy)
from Docker Hub, e.g., using

`$ docker pull flex/gns3-proxy`

`$ docker run -p 0.0.0.0:14080:14080/tcp flex/gns3-proxy`

You can find [sample scripts](https://github.com/srieger1/gns3-proxy/tree/develop/scripts/docker-container-example) to run and manage
the container in the scripts directory of this repository.

Also, you can install the gns3-proxy from [PyPI](https://pypi.org/project/gns3-proxy/) using

`$ pip install gns3-proxy`

Preparing GNS3 server backends
------------------------------

The only change necessary in the GNS3 server backends, is to edit the regular
gns3_server.conf (available in the appliance terminal and, e.g., used to
change username password etc., see also
[GNS3 server configuration file](https://docs.gns3.com/1f6uXq05vukccKdMCHhdki5MXFhV8vcwuGwiRvXMQvM0/index.html))
and change the hostname from 0.0.0.0 to the IP address the server should
listen on, e.g.:

`host = 192.168.1.100`

After you changed the config of the GNS3 backend servers and restarted them, configure gns3_proxy_config.ini based
on your needs and run gns3_proxy.py. You can then, configure GNS3-GUI to use the proxy as a remote GNS3 server. 
By default, the proxy listens on 0.0.0.0 and TCP port 14080.

Configuration
-------------

Settings of the proxy are stored in [gns3_proxy_config.ini](https://github.com/srieger1/gns3-proxy/tree/develop/gns3_proxy_config.ini).
The `[proxy]` section contains following parameters for gns3-proxy:

- **hostname:** IP address or corresponding hostname the proxy should bind to, listening for incoming requests (default: 0.0.0.0)
- **port** TCP port the proxy will listen on (default: 14080)
- **backend_user** Username to use to connect to GNS3 server backend (default: admin, standard GNS3 server user)
- **backend_password** Password to use to connect to GNS3 server backend (default: password)
- **backend_port** TCP port the backend servers listen on (default: 3080, standard GNS3 server port)
- **default_server** Default server backend to use if no individual mapping for the user was found. Can be omitted to use explicit mapping (default: gns3-1)
- **backlog** Backlog of the proxy. Increase to allow the processing of more concurrent requests (default: 1000)
- **server-recvbuf-size** Server receive buffer size (TCP socket) of the proxy in bytes. Increase this value for better performance of large responses from backend servers (default: 8192, recommended for production: 1048576)
- **client-recvbuf-size** Client receive buffer size (TCP socket) of the proxy in bytes. Increase this value for better performance of large requests from clients (default: 8192, recommended for production: 1048576)
- **open-file-limit** Maximum number of parallel open files (socket fds) of the proxy (default: 1024)
- **log-level** Log level. Increase to DEBUG for debugging output. (default: INFO)

The `[servers]` section contains the defined backend servers (server_name=ip_address), e.g.:

```
gns3-1=192.168.76.205
gns3-2=192.168.76.206
```

The `[users]` section defines the users allowed to access the proxy and their passwords (username=password), e.g.:

```
user1=pass1
user2=pass2
```

The `[mapping]` section maps users to the backend servers (mapping_id="user regexp":"server_name"), e.g.: 

```
mapping1="user2":"gns3-2"
mapping2="user(.*)":"gns3-1"
```

The `[deny]` section defines requests that should be filtered and hence denied by the proxy (rule_id="user regexp":"http_request_method":"url regexp":"header regexp":"body regexp"), e.g. to deny modification to existing projects as well as deletion and creation of projects:

```
rule1="user(.*)":"POST":"(.*)/projects$":"":""
rule2="user(.*)":"POST":"(.*)/nodes$":"":""
rule3="user(.*)":"POST":"(.*)/links$":"":""
rule4="user(.*)":"POST":"(.*)/drawings$":"":""
rule5="user(.*)":"POST":"(.*)/appliances/(.*)":"":""
rule6="user(.*)":"DELETE":"":"":""
```

Deploying and managing projects on gns3-proxy backends
------------------------------------------------------

[gns3_proxy_replicate_projects.py](https://github.com/srieger1/gns3-proxy/blob/develop/gns3_proxy_replicate_projects.py) facilitates the replication of projects across backend servers.
Command syntax is:

```
usage: gns3_proxy_replicate_projects.py [-h] [--config-file CONFIG_FILE]
                                        [--delete-target-project] [--force]
                                        [--inject-replication-note]
                                        [--log-level LOG_LEVEL]
                                        (--project-id PROJECT_ID | --project-name PROJECT_NAME)
                                        [--regenerate-mac-address REGENERATE_MAC_ADDRESS]
                                        --source-server SOURCE_SERVER
                                        --target-server TARGET_SERVER
```

The provided example [crontab](https://github.com/srieger1/gns3-proxy/blob/develop/gns3_proxy_crontab) contains examples to 
use gns3_proxy_replicate_projects.py. For example:

```
gns3_proxy_replicate_projects.py --source gns3-master --target "gns3-(.*)" --project-name "KommProt(.*)" --regenerate-mac-address "02:01:00:(.*)" --force 
```

will replicate all GNS3 project names beginning with "KommProt" from the backend server gns3-master as the source to
all backend servers matching the regular expression "gns3-.(.*)". The option --force tells the utility to overwrite existing
projects with the same name on the targets without further notice. The option --regenerate-mac-address searches for the
given MAC address in the projects and creates a new locally administered MAC address. This is especially necessary for
links to cloud node types in the project. Otherwise all projects will use the same address leading to duplicate MAC and
consequently duplicated IP addresses. 

[gns3_proxy_manage_projects.py](https://github.com/srieger1/gns3-proxy/blob/develop/gns3_proxy_manage_projects.py) facilitates the management of projects on backend servers.
Command syntax is:

```
usage: gns3_proxy_manage_projects.py [-h] [--config-file CONFIG_FILE]
                                     [--force] [--log-level LOG_LEVEL]
                                     (--project-id PROJECT_ID | --project-name PROJECT_NAME)
                                     (--export-to-dir EXPORT_TO_DIR | --import-from-file IMPORT_FROM_FILE | --show | --delete | --start | --stop)
                                     --target-server TARGET_SERVER
```

The provided example [crontab](https://github.com/srieger1/gns3-proxy/blob/develop/gns3_proxy_crontab) contains examples to 
use gns3_proxy_manage_projects.py. For example:

```
gns3_proxy_manage_projects.py --show --project-name "(.*)" --target "(.*)" 
```

will show the status of all projects on all backend server.

```
gns3_proxy_manage_projects.py --start --project-name TestProject --target gns3-1 
```

will start the project with the name TestProject on the server gns3-1 defined as a backend in gns3_proxy_config.ini.
Can be used, e.g., together with cron to start the project ahead of time for lab sessions or courses, avoiding
waiting for projects to be ready for use when students take the lab.

```
gns3_proxy_manage_projects.py --export-to-dir . --project-name TestProject --target gns3-1 
```

will export the project TestProject from gns3-1 to a ZIP file that can be used as a backup, e.g. to import later using
GNS3 GUI, or --import-from-file option, like:

```
gns3_proxy_manage_projects.py --import-from-file project.zip --project-id f1d1e2b8-c41f-42cf-97d4-513f3fd01cd2 --target gns3-1 
```

will import GNS3 project exported in file project.zip to backend server gns3-1. The specified project-id (must be a valid UUID v4
in GNS3) will be used for the import.
