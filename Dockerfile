FROM python:3-alpine
LABEL de.hs-fulda.netlab.name="flex/gns3-proxy" \
      de.hs-fulda.netlab.description="GNS3 Proxy (based on proxy.py by Abhinav Singh)" \
#      de.hs-fulda.netlab.build-date="" \
      de.hs-fulda.netlab.url="https://github.com/srieger1/gns3-proxy" \
      de.hs-fulda.netlab.vcs-url="https://github.com/srieger1/gns3-proxy" \
#      de.hs-fulda.netlab.vcs-ref="" \
      de.hs-fulda.netlab.docker.cmd="docker run -it --rm -p 14080:14080 flex/gns3-proxy"

RUN apk add --update --no-cache openssh-client
RUN apk add --no-cache bash
RUN apk add --no-cache nano

WORKDIR /home/gns3_proxy

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY gns3_proxy.py /usr/local/bin/
COPY gns3_proxy_*.py /usr/local/bin/
COPY setup-backend.sh /usr/local/bin/
COPY config-templates ./config-templates
COPY gns3_proxy_config.ini ./
COPY gns3_proxy_crontab /var/spool/cron/crontabs/gns3_proxy

EXPOSE 14080/tcp

RUN chmod +x /usr/local/bin/*.py
RUN chmod +x /usr/local/bin/setup-backend.sh

CMD ["sh", "-c", "crond && gns3_proxy.py"]
