#!/bin/sh
# IRIS network/service fallback, injected into the guest rootfs at /etc/init.d/iris_net_fix.
# Two invocation styles must both work:
#   - OpenWrt-style images source it via /etc/rc.common (START=99; boot())
#   - arm64 generic-kernel channel calls it directly: /bin/sh /etc/init.d/iris_net_fix &
# Everything that matters is logged to /dev/console so it lands in qemu.serial.log.

START=99

log() {
    echo "IRIS-NETFIX: $*" > /dev/console 2>/dev/null
}

has_ip() {
    ifconfig "$1" 2>/dev/null | grep -q "inet addr"
}

# Busybox netstat is unreliable here: several vendor builds print the service
# name ("0.0.0.0:http") instead of the numeric port, so a ':0050' grep reports
# a live web server as absent and IRIS ends up launching a second one (which
# then logs "Cannot bind to address *:80, errno 98"). Process presence is what
# the vendor's own supervisor checks, so use that first.
web_running() {
    for p in goahead boa lighttpd httpd uhttpd thttpd apache2 nginx; do
        pidof "$p" >/dev/null 2>&1 && return 0
        ps 2>/dev/null | grep -w "$p" | grep -v grep >/dev/null 2>&1 && return 0
    done
    return 1
}

port80_listening() {
    netstat -lan 2>/dev/null | grep -qE '[:.](80|0050|http)([[:space:]]|$)'
}

fixup() {
    log "start: brief pause before taking over the network"
    # Short grace period: the vendor chain (configd) may configure itself. The IP
    # is the critical path for reachability, so fix it early and only wait longer
    # before deciding whether a web server has to be launched.
    sleep 15

    HAS_IP=0
    for iface in eth0 eth1 br0 br-lan br1 ra0; do
        if has_ip "$iface"; then
            HAS_IP=1
            log "$iface already has an IP (vendor configured it)"
        fi
    done

    if [ "$HAS_IP" = "0" ]; then
        log "no interface has an IP, assigning fallback 192.168.1.1 to eth0"
        ifconfig eth0 192.168.1.1 netmask 255.255.255.0 up 2>/dev/null || true
    fi

    ifconfig eth0 up 2>/dev/null || true

    # Vendor firewalls block rehosted-internal traffic by default.
    iptables -F 2>/dev/null || true
    iptables -P INPUT ACCEPT 2>/dev/null || true
    iptables -P OUTPUT ACCEPT 2>/dev/null || true
    iptables -P FORWARD ACCEPT 2>/dev/null || true

    for iface in lo eth0; do
        has_ip "$iface" && log "final: $iface up with IP"
    done

    # Remote shell even if the serial console is unavailable to the operator.
    if [ ! -e /etc/rc.common ]; then
        log "starting telnetd on port 7002 (fallback shell)"
        telnetd -l /bin/sh -p 7002 &
    fi

    # Web: vendor config may have prevented its own web server from starting
    # (e.g. configd needs a UBIFS /var/config that does not exist under rehosting).
    # Wait a bit longer first so the vendor's own server wins the race for :80.
    sleep 30
    log "probing for a web server on :80"
    if web_running; then
        log "vendor web server is already running, leaving :80 to it"
    elif port80_listening; then
        log "something else already listens on :80"
    elif [ -x /opt/goahead/goahead ] && [ -f /opt/goahead/route.txt ]; then
        log "web not listening on :80, launching goahead"
        /opt/goahead/goahead --home /opt/goahead --route /opt/goahead/route.txt &
    elif [ -x /usr/bin/boa ] && [ -f /etc/boa/boa.conf ]; then
        log "web not listening on :80, launching boa"
        /usr/bin/boa -c /etc/boa &
    else
        log "no web server fallback available"
    fi

    log "done"
}

# rc.common defines add_func before sourcing: register boot() there, run fixup
# directly when invoked as a plain script (arm64 channel).
if type add_func >/dev/null 2>&1; then
    add_func boot
else
    fixup
fi
