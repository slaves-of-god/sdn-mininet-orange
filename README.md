# sdn-mininet-orange
# SDN-Mininet-ARP-Handler
### Centralized ARP Management via SDN Controller · *The Orange Problem*

---

| Field | Details |
|---|---|
| **Name** | Sudeeksh Nayak |
| **SRN** | PES1UG24CS474 |
| **Section** | 4H |


---

## What Is This?

Traditional Ethernet networks handle ARP (Address Resolution Protocol) by **flooding** requests across all ports — a wasteful broadcast that degrades performance as networks scale. This project eliminates that bottleneck by moving ARP intelligence **up into the SDN controller**.

The result: a controller-centric ARP handler that intercepts requests, learns MAC addresses dynamically, forges targeted replies, and installs hardware-level flow rules — reducing broadcast traffic to near zero after the first handshake.

---

## Environment & Topology

```
  [h1] ──┐
  [h2] ──┤── [s1 :: OpenFlow Switch] ── POX Controller (remote)
  [h3] ──┘
```

| Component | Version / Detail |
|---|---|
| OS | Ubuntu Linux (VM) |
| Network Emulator | Mininet |
| Topology | `single,3` — 1 switch, 3 hosts |
| SDN Controller | POX (OpenFlow 1.0) |
| Language | Python 3 |

**Why `single,3`?**
It's the minimal topology to expose ARP flooding, demonstrate controller interception, and validate hardware flow installation — without adding routing complexity that would obscure the core mechanism.

---

## Controller Logic — `arp_handler.py`

### 1. Dynamic MAC Learning
The controller listens for `PacketIn` events. Every incoming ARP request yields a sender IP → MAC mapping, which is stored in `self.arp_table`. The table grows silently as hosts communicate.

### 2. ARP Interception & Reply Forging
Once a target MAC is known, the controller **does not flood**. It:
- Crafts a synthetic `ARP Reply` packet
- Sends it directly back out the **ingress port** using `of.OFPP_IN_PORT`
- Bypasses OpenFlow loop-prevention that would otherwise drop it

Unknown targets fall back to a controlled flood, which populates the table for next time.

### 3. Proactive Flow Rule Installation
After MAC resolution, the first ICMP ping triggers `ofp_flow_mod` rules pushed to the switch hardware:

```
idle_timeout  = 60 seconds
hard_timeout  = 120 seconds
priority      = 10
```

Subsequent packets are forwarded at **line-rate** without ever reaching the controller.

### 4. Graceful Table Eviction *(added)*
Flow rules with `idle_timeout=60` automatically evict stale entries. The controller also monitors `FlowRemoved` events to purge corresponding `arp_table` entries, keeping the controller state consistent with the switch.

### 5. Collision-Safe ARP Table Updates *(added)*
If a host changes its IP (e.g., DHCP reassignment during testing), the controller detects the MAC mismatch and **overwrites** the stale entry with a timestamped log, preventing stale mappings from causing silent packet drops.

### 6. Per-Port Traffic Counters *(added)*
The controller tracks `PacketIn` frequency per ingress port. Ports exceeding a configurable threshold within a rolling window are flagged in logs — an early indicator of ARP storm or misbehaving hosts.

---

## Running the Project

### Step 1 — Launch the Controller

```bash
cd ~/pox
./pox.py arp_handler
```

### Step 2 — Start the Mininet Topology

```bash
# Clear any stale network state first
sudo mn -c

# Launch topology with remote POX controller
sudo mn --topo single,3 --controller remote
```

### Step 3 — Trigger ARP Resolution

```bash
mininet> h1 ping -c 5 h2
```

### Step 4 — Verify Flow Rules Were Installed

```bash
mininet> dpctl dump-flows
```

### Step 5 — Inspect ARP Table State *(optional)*

```bash
# From the POX console, query the in-memory ARP table
mininet> h3 arping h1
```

---

## Performance Analysis

### Observed Latency Profile

| Ping # | Latency | Reason |
|---|---|---|
| 1st | ~1086 ms | Controller processes `PacketIn`, floods unknown ARP, forges reply, pushes `flow_mod` |
| 2nd | ~12 ms | Rule partially warm; switch still consulting controller for flow misses |
| 3rd–5th | ~0.113 ms | Full line-rate forwarding; controller not involved |

### Why the 1st Ping Is Slow

The control-plane round-trip involves:
1. Switch sends `PacketIn` to controller over TCP
2. Controller parses ARP, checks table (miss)
3. Controller floods → receives ARP reply from target
4. Controller learns target MAC, forges ARP reply to requester
5. Controller installs `flow_mod` on switch
6. ICMP Echo finally traverses the switch

All subsequent pings skip steps 1–5 entirely.

### Broadcast Reduction

In a standard switch with N hosts, each ARP request generates **N-1 broadcast frames**. With this controller:
- 1st request: broadcast occurs once to populate the table
- Every subsequent request to a known host: **0 broadcast frames**

For a 100-host network, this eliminates ~99% of ARP-related broadcast traffic after warm-up.

---

## Proof of Execution

### 1. Controller Logs — MAC Learning & ARP Interception
Demonstrates initial flood, MAC learning, direct ARP replies, and flow rule pushes.

<img width="884" height="241" alt="Screenshot 2026-04-15 090745" src="https://github.com/user-attachments/assets/b9710583-70a9-4400-b8eb-0a18d750033a" />
<img width="953" height="928" alt="Screenshot 2026-04-15 090700" src="https://github.com/user-attachments/assets/b426ac6f-4c39-4cac-9db1-815a52c819cd" />


### 2. ICMP Ping Statistics — Latency Drop
Proves the transition from controller-handled (~1086 ms) to hardware-forwarded (~0.113 ms).

<img width="925" height="272" alt="image" src="https://github.com/user-attachments/assets/ebbd6f0b-70b8-4aa9-9416-2fd2681b2101" />


### 3. OpenFlow Table Dump — `dpctl dump-flows`
Validates that explicit match/action rules with `idle_timeout=60` are installed on `s1`.

<img width="940" height="273" alt="image" src="https://github.com/user-attachments/assets/14ae15fb-3c22-405d-9231-536423cbe89a" />


### 4. Wireshark Packet Trace
Captures the wire-level sequence: ARP Broadcast → Controller ARP Reply → ICMP Echo sequence.
<img width="1822" height="367" alt="image" src="https://github.com/user-attachments/assets/bde266cd-e738-4901-92b3-97bf535d593d" />


---

## Key Takeaways

- **ARP flooding is a solved problem in SDN.** Controller-based MAC learning reduces broadcast to a one-time cost per new host pair.
- **Flow rules are the multiplier.** The controller does expensive work once; the switch hardware does the fast work forever (until timeout).
- **Consistency matters.** Stale ARP entries cause silent failures. The `FlowRemoved` listener ensures controller state tracks switch state.
- **Observability is free.** Per-port counters and timestamped ARP logs cost negligible CPU but make debugging dramatically faster.

---

*Built with POX · Mininet · OpenFlow 1.0 · Python 3*