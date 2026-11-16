# iptables 1.8.10 (backend nf_tables) compilado ESTÁTICO: Docker lo necesita (redes y DNS internos entre servicios) y
# los sistemas mínimos sin internet no lo traen. Lo usa construir_paquete.sh; el binario va en docker/ del paquete.
FROM alpine:3.20
RUN apk add --no-cache build-base linux-headers pkgconf libmnl-dev libmnl-static libnftnl-dev libnftnl-static curl xz bison flex binutils
WORKDIR /src
RUN curl -fsSL https://www.netfilter.org/projects/iptables/files/iptables-1.8.10.tar.xz | tar xJ
WORKDIR /src/iptables-1.8.10
RUN ./configure --prefix=/opt/iptables --enable-static --disable-shared LDFLAGS="-static" \
 && make -j"$(nproc)" LDFLAGS="-all-static" && make install \
 && mkdir -p /salida/iptables && cp /opt/iptables/sbin/xtables-nft-multi /salida/iptables/ && strip /salida/iptables/xtables-nft-multi
