#!/bin/bash

#FOR SERVER in seq 6-9
#for TISCH in seq 1 2

#PROJECT_BASENAME="NMM-Uebung-IPAM-Tisch"
#PROJECT_FILE_BASENAME="NMM-Übung-IPAM_Tisch"
PROJECT_BASENAME="f1d1e2b8-c41f-42cf-97d4-513f3fd01cd2"
PROJECT_FILE_BASENAME="KommProtUeb3"
TISCH_NR=""
MAC_ADDRESS="00:1c:d2:e3:9e:00"

echo "Syncing project: $PROJECT_BASENAME ($PROJECT_FILE_BASENAME$TISCH_NR.gns3)"
PROJECT_SIZE=$(du -hs /opt/gns3/projects/$PROJECT_BASENAME$TISCH_NR | cut -f 1)
echo "Project size: $PROJECT_SIZE"

#SERVER_NR=6
for SERVER_NR in `seq 5 9`; do

  echo "####################################"
  echo "##                                ##"
  echo "## Syncing GNS3 192.168.76.20$SERVER_NR    ##"
  echo "##                                ##"
  echo "####################################"

  #for TISCH_NR in `seq 3 3`; do

    #echo "#### Running rsync for Tisch: $TISCH_NR ####"

    # --progess
    rsync -ah --delete /opt/gns3/projects/$PROJECT_BASENAME$TISCH_NR root@192.168.76.20$SERVER_NR:/opt/gns3/projects
    #ssh root@192.168.76.20$SERVER_NR rm -rf /opt/gns3/projects/${PROJECT_BASENAME}2

    NEW_MAC_ADDRESS=$(echo -n 02:01:00; dd bs=1 count=3 if=/dev/random 2>/dev/null |hexdump -v -e '/1 ":%02x"')
    echo "#### Changing MAC address $MAC_ADDRESS to $NEW_MAC_ADDRESS to avoid collisions when using DHCP on connection to NetLab ####"

    ssh root@192.168.76.20$SERVER_NR sed -i.bak s/$MAC_ADDRESS/$NEW_MAC_ADDRESS/g /opt/gns3/projects/$PROJECT_BASENAME$TISCH_NR/$PROJECT_FILE_BASENAME$TISCH_NR.gns3
  #done

  echo "#### Restarting GNS3 service ####"

  #ssh root@192.168.76.20$SERVER_NR service gns3 restart
  ssh root@192.168.76.20$SERVER_NR df -h

  ssh root@192.168.76.20$SERVER_NR reboot
done
