#!/bin/sh
# Generated: kernel data plane for the FRR leaf.
set -u
sysctl -qw net.ipv4.ip_forward=1
sysctl -qw net.ipv4.fib_multipath_hash_policy=1
sysctl -qw net.ipv4.conf.all.rp_filter=0
VRF=1
ip link add vrf-probe type vrf table 999 2>/dev/null && ip link del vrf-probe || VRF=0
echo "vrf-support=$VRF" > /tmp/vrf-support
# tenant red
ip link add br10010 type bridge
ip link set br10010 addrgenmode none 2>/dev/null || true
ip link add vx10010 type vxlan id 10010 dstport 4789 local 10.0.1.3 nolearning
ip link set vx10010 master br10010 addrgenmode none
ip link set vx10010 type bridge_slave neigh_suppress on learning off
ip link set eth1 master br10010
ip link set eth1 up; ip link set vx10010 up; ip link set br10010 up
if [ "$VRF" = 1 ]; then
  ip link add vrf-red type vrf table 101
  ip link set vrf-red up
  ip link add br10100 type bridge
  ip link set br10100 master vrf-red addrgenmode none
  ip link add vx10100 type vxlan id 10100 dstport 4789 local 10.0.1.3 nolearning
  ip link set vx10100 master br10100 addrgenmode none
  ip link set vx10100 up; ip link set br10100 up
  ip link set br10010 master vrf-red
  ip link set br10010 address 00:00:5e:00:01:01
  ip addr add 172.16.10.1/24 dev br10010
fi
# tenant blue
ip link add br20010 type bridge
ip link set br20010 addrgenmode none 2>/dev/null || true
ip link add vx20010 type vxlan id 20010 dstport 4789 local 10.0.1.3 nolearning
ip link set vx20010 master br20010 addrgenmode none
ip link set vx20010 type bridge_slave neigh_suppress on learning off
ip link set eth2 master br20010
ip link set eth2 up; ip link set vx20010 up; ip link set br20010 up
if [ "$VRF" = 1 ]; then
  ip link add vrf-blue type vrf table 201
  ip link set vrf-blue up
  ip link add br20100 type bridge
  ip link set br20100 master vrf-blue addrgenmode none
  ip link add vx20100 type vxlan id 20100 dstport 4789 local 10.0.1.3 nolearning
  ip link set vx20100 master br20100 addrgenmode none
  ip link set vx20100 up; ip link set br20100 up
  ip link set br20010 master vrf-blue
  ip link set br20010 address 00:00:5e:00:01:01
  ip addr add 172.16.20.1/24 dev br20010
fi
