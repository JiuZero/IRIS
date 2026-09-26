#!/bin/sh /etc/rc.common

START=99

boot() {
    sleep 5

    if ! ifconfig eth0 2>/dev/null | grep -q "inet addr"; then
        ifconfig eth0 192.168.1.1 netmask 255.255.255.0 up 2>/dev/null || true
    fi

    if brctl show 2>/dev/null | grep -q "br-lan"; then
        if ! ifconfig br-lan 2>/dev/null | grep -q "inet addr"; then
            ifconfig br-lan 192.168.1.1 netmask 255.255.255.0 up 2>/dev/null || true
        fi
    fi

    iptables -F 2>/dev/null || true
    iptables -P INPUT ACCEPT 2>/dev/null || true
    iptables -P OUTPUT ACCEPT 2>/dev/null || true
    iptables -P FORWARD ACCEPT 2>/dev/null || true
}