FROM python:3-alpine
LABEL de.hs-fulda.netlab.name="flex/gns3-proxy" \
      de.hs-fulda.netlab.description="GNS3 Proxy (based on proxy.py by Abhinav Singh)" \
#      de.hs-fulda.netlab.build-date="" \
      de.hs-fulda.netlab.url="https://github.com/srieger1/gns3-proxy" \
      de.hs-fulda.netlab.vcs-url="https://github.com/srieger1/gns3-proxy" \
#      de.hs-fulda.netlab.vcs-ref="" \
      de.hs-fulda.netlab.docker.cmd="docker run -it --rm -p 14080:14080 flex/gns3-proxy"

COPY gns3-proxy.py /app/
COPY gns3-proxy-config.ini /app/
EXPOSE 14080/tcp

RUN chmod +x /app/gns3-proxy.py

WORKDIR /app
ENTRYPOINT [ "./gns3-proxy.py" ]

