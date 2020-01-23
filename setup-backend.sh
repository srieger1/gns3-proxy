#!/bin/bash

# setup-backend.sh
# HS-Fulda - sebastian.rieger@informatik.hs-fulda.de
#
# changelog:
# V0.1    initial version

# usage
if [ ! $# -eq 2 ] ; then
  echo -e "usage: $0 <gns3 proxy config file> <ip address of backend to configure>, e.g.:\n"
  echo "$0 ./gns3_proxy_config.ini 192.168.0.100"
  exit -1
fi

GNS3_PROXY_CONFIG_FILE=$1

BACKEND_IP_ADDRESS=$2

BACKEND_USERNAME=$(cat $GNS3_PROXY_CONFIG_FILE | grep backend_user | cut -d "=" -f 2)
BACKEND_PASSWORD=$(cat $GNS3_PROXY_CONFIG_FILE | grep backend_password | cut -d "=" -f 2)
BACKEND_PORT=$(cat $GNS3_PROXY_CONFIG_FILE | grep backend_port | cut -d "=" -f 2)

if [ ! -f "$HOME/.ssh/id_rsa" ]; then
  echo "Current user has no ssh key. Creating ssh key in ~/.ssh/id_rsa"
  ssh-keygen -q -f $HOME/.ssh/id_rsa -N ""
fi

echo "Copying ssh pub key to new backend server..."
echo "User username gns3 to login to the backend server at $BACKEND_IP_ADDRESS, please enter the password of this user on the backend (default: gns3)."
ssh-copy-id -f gns3@$BACKEND_IP_ADDRESS

# modify and copy gns3_server.conf template
sed s/"<BACKEND_IP_ADDRESS>"/"$BACKEND_IP_ADDRESS"/g config-templates/gns3_server.conf >config-templates/gns3_server.conf.$BACKEND_IP_ADDRESS
sed -i s/"<BACKEND_PORT>"/"$BACKEND_PORT"/g config-templates/gns3_server.conf.$BACKEND_IP_ADDRESS
sed -i s/"<BACKEND_USERNAME>"/"$BACKEND_USERNAME"/g config-templates/gns3_server.conf.$BACKEND_IP_ADDRESS
sed -i s/"<BACKEND_PASSWORD>"/"$BACKEND_PASSWORD"/g config-templates/gns3_server.conf.$BACKEND_IP_ADDRESS

ssh gns3@$BACKEND_IP_ADDRESS cp -a /home/gns3/.config/GNS3/2.2/gns3_server.conf /home/gns3/.config/GNS3/2.2/gns3_server.conf.bak
scp config-templates/gns3_server.conf.$BACKEND_IP_ADDRESS gns3@$BACKEND_IP_ADDRESS:/home/gns3/.config/GNS3/2.2/gns3_server.conf

# restart gns3 done
ssh gns3@$BACKEND_IP_ADDRESS sudo service gns3 restart
