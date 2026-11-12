# RoCE lossless class: PFC + ECN (not exercised in the lab)

Containers have no hardware queues, so the lab cannot pause or mark traffic.
This directory holds the configuration a hardware fabric would carry, and what
each part does. `sonic-roce-qos.json` is a SONiC CONFIG_DB fragment for a
backend switch port (`Ethernet0`; repeat the `PORT_QOS_MAP`, `QUEUE` and
`PFC_WD` entries per backend port), loaded with
`sonic-cfggen -j sonic-roce-qos.json --write-to-db && config save -y`.
SONiC derives the lossless buffer profiles for priority 3 from `pfc_enable`
and the port's cable length, so the fragment carries no `BUFFER_*` tables.
`host-mlx5.sh` is the matching NIC setting on a GPU host.
**Neither has been applied to hardware** - validate table and field names on
the target SONiC release and platform.

## Traffic classes

| Class | DSCP | Traffic class / queue | PFC | ECN | Share |
| --- | --- | --- | --- | --- | --- |
| RoCEv2 data | 26 | TC3 / queue 3 | on (lossless) | WRED-ECN, Kmin 150 KB, Kmax 1500 KB, Pmax 10% | 95% (WRR) |
| CNP (congestion notification) | 48 | TC6 / queue 6 | off | off | strict priority |
| Everything else | others | TC0 / queue 0 | off | off | 5% |

## What each piece does on hardware

1. **Classification.** The NIC marks RoCEv2 packets DSCP 26; the switch maps
   DSCP 26 to forwarding class TC3 on ingress. CNPs (DSCP 48) go to a strict
   priority queue so congestion signals are never delayed behind data.
2. **ECN first.** When queue 3 grows past Kmin, the switch starts marking CE
   with a probability that rises to Pmax at Kmax. The receiver NIC returns a
   CNP; the sender's DCQCN rate limiter backs off. Done right, ECN keeps
   queues short enough that PFC almost never fires.
3. **PFC as the backstop.** If queue 3's ingress buffer still crosses the
   XOFF threshold, the switch sends a PFC pause frame for priority 3 only
   upstream. Other priorities keep flowing. Headroom must absorb what is in
   flight during the pause (cable length × line rate × 2 + MTU terms).
4. **Watchdog.** A PFC watchdog drops traffic on a queue that stays paused
   for longer than 200 ms, breaking pause storms and deadlocks at the cost of
   a lossy event, which RoCE recovers from with go-back-N.
5. **Trust mode on the host.** The NIC must trust DSCP (not PCP), since the
   fabric is routed L3 and has no VLAN tags on the backend.

## Why the lab still carries it

The lab proves the routing design. The QoS design is reviewed here and in
`network/design-note.md` (bottlenecks and congestion control), and should be
the first thing tested on real switches.
