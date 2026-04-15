from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.packet.ethernet import ethernet
from pox.lib.packet.arp import arp
import time

log = core.getLogger()

ARP_TIMEOUT = 120  # seconds

class EnhancedArpController(object):
    def __init__(self, connection):
        self.connection = connection
        connection.addListeners(self)
        
        # IP -> (MAC, timestamp)
        self.arp_table = {}
        
        # MAC -> Port
        self.mac_to_port = {}
        
        # Port traffic counter
        self.port_stats = {}

    def _clean_arp_table(self):
        """Remove expired ARP entries"""
        current_time = time.time()
        expired = [ip for ip, (mac, ts) in self.arp_table.items()
                   if current_time - ts > ARP_TIMEOUT]
        
        for ip in expired:
            log.info("Removing expired ARP entry: %s", ip)
            del self.arp_table[ip]

    def _handle_PacketIn(self, event):
        packet = event.parsed
        if not packet.parsed:
            return

        in_port = event.port

        # Update port stats
        self.port_stats[in_port] = self.port_stats.get(in_port, 0) + 1

        # Learn MAC → Port
        self.mac_to_port[packet.src] = in_port

        # Clean ARP table periodically
        self._clean_arp_table()

        # ---------------- ARP HANDLING ----------------
        if packet.type == packet.ARP_TYPE:
            arp_pkt = packet.payload

            # Update ARP cache with timestamp
            self.arp_table[arp_pkt.protosrc] = (arp_pkt.hwsrc, time.time())

            if arp_pkt.opcode == arp.REQUEST:
                if arp_pkt.protodst in self.arp_table:
                    log.info("[ARP HIT] %s is known", arp_pkt.protodst)

                    dst_mac = self.arp_table[arp_pkt.protodst][0]

                    arp_reply = arp()
                    arp_reply.opcode = arp.REPLY
                    arp_reply.hwsrc = dst_mac
                    arp_reply.hwdst = arp_pkt.hwsrc
                    arp_reply.protosrc = arp_pkt.protodst
                    arp_reply.protodst = arp_pkt.protosrc

                    eth = ethernet(type=packet.type,
                                   src=dst_mac,
                                   dst=packet.src)
                    eth.set_payload(arp_reply)

                    msg = of.ofp_packet_out()
                    msg.data = eth.pack()
                    msg.actions.append(of.ofp_action_output(port=of.OFPP_IN_PORT))
                    msg.in_port = in_port
                    self.connection.send(msg)

                else:
                    log.info("[ARP MISS] Flooding request for %s", arp_pkt.protodst)

                    # Selective flood (avoid sending back to input port)
                    msg = of.ofp_packet_out()
                    msg.data = event.ofp
                    msg.in_port = in_port
                    msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
                    self.connection.send(msg)

            elif arp_pkt.opcode == arp.REPLY:
                log.info("[ARP LEARN] %s -> %s", arp_pkt.protosrc, arp_pkt.hwsrc)

        # ---------------- NORMAL L2 FORWARDING ----------------
        else:
            if packet.dst in self.mac_to_port:
                out_port = self.mac_to_port[packet.dst]

                log.info("[FLOW INSTALL] %s → %s via port %s",
                         packet.src, packet.dst, out_port)

                flow_msg = of.ofp_flow_mod()
                flow_msg.match = of.ofp_match.from_packet(packet, in_port)
                flow_msg.idle_timeout = 30
                flow_msg.hard_timeout = 90
                flow_msg.priority = 10

                flow_msg.actions.append(of.ofp_action_output(port=out_port))

                # Optimization: use buffer_id if available
                if event.ofp.buffer_id != -1:
                    flow_msg.buffer_id = event.ofp.buffer_id
                    self.connection.send(flow_msg)
                else:
                    self.connection.send(flow_msg)

                    # Send packet manually
                    msg = of.ofp_packet_out()
                    msg.data = event.ofp
                    msg.in_port = in_port
                    msg.actions.append(of.ofp_action_output(port=out_port))
                    self.connection.send(msg)

            else:
                log.info("[UNKNOWN DST] Flooding packet")

                msg = of.ofp_packet_out()
                msg.data = event.ofp
                msg.in_port = in_port
                msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
                self.connection.send(msg)


def launch():
    def start_switch(event):
        log.info("Enhanced Controller active on %s", event.connection)
        EnhancedArpController(event.connection)

    core.openflow.addListenerByName("ConnectionUp", start_switch)