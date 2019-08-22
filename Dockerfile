FROM python:3-alpine
LABEL de.hs-fulda.netlab.name="flex/gns3-proxy" \
      de.hs-fulda.netlab.description="GNS3 Proxy (based on proxy.py by Abhinav Singh)" \
#      de.hs-fulda.netlab.build-date="" \
      de.hs-fulda.netlab.url="https://github.com/srieger1/gns3-proxy" \
      de.hs-fulda.netlab.vcs-url="https://github.com/srieger1/gns3-proxy" \
#      de.hs-fulda.netlab.vcs-ref="" \
      de.hs-fulda.netlab.docker.cmd="docker run -it --rm -p 14080:14080 flex/gns3-proxy"

RUN adduser -S gns3_proxy

COPY gns3_proxy.py /usr/local/bin/
COPY gns3_proxy_*.py /usr/local/bin/
COPY gns3_proxy_config.ini /home/gns3_proxy/
COPY gns3_proxy_crontab /var/spool/cron/crontabs/gns3_proxy

EXPOSE 14080/tcp

RUN chmod +x /usr/local/bin/*.py

RUN pip install requests

WORKDIR /home/gns3_proxy
CMD ["sh", "-c", "crond && gns3_proxy.py"]
