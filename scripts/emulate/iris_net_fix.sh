#!/bin/sh /etc/rc.common

START=99

boot() {
    sleep 5

    # Check if any interface already has an IP (firmware configured its own network)
    HAS_IP=0
    for iface in eth0 br0 br-lan br1; do
        if ifconfig $iface 2>/dev/null | grep -q "inet addr"; then
            HAS_IP=1
            break
        fi
    done

    # Only assign IP if no interface has one
    if [ "$HAS_IP" = "0" ]; then
        ifconfig eth0 192.168.1.1 netmask 255.255.255.0 up 2>/dev/null || true
    fi

    # Ensure eth0 is up
    ifconfig eth0 up 2>/dev/null || true

    # Flush iptables to allow all traffic
    iptables -F 2>/dev/null || true
    iptables -P INPUT ACCEPT 2>/dev/null || true
    iptables -P OUTPUT ACCEPT 2>/dev/null || true
    iptables -P FORWARD ACCEPT 2>/dev/null || true
}