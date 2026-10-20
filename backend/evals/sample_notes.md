# Subnetting basics

An IPv4 address is 32 bits, written as four decimal numbers separated by dots. A subnet mask splits the address into a network part and a host part. The mask is a run of ones followed by a run of zeros, and it is usually written as a prefix length such as /24, which means the first 24 bits identify the network.

Every subnet reserves two addresses. The first address, with all host bits set to zero, names the network itself. The last address, with all host bits set to one, is the broadcast address. Neither can be given to a device. The number of usable host addresses in a subnet is therefore 2^h - 2, where h is the number of host bits. A /24 has 8 host bits, so it offers 254 usable addresses. A /30 has 2 host bits and offers exactly 2 usable addresses, which is why it is a common choice for a link between two routers.

To find the network address of a host, write the address and the mask in binary and apply a bitwise AND. Every bit where the mask has a one is copied from the address, and every bit where the mask has a zero becomes zero. For example, 192.168.10.77 with the mask 255.255.255.192 (/26) gives the network 192.168.10.64, because 77 in binary is 01001101 and the top two bits survive the mask, leaving 01000000, which is 64.

Borrowing bits is how a larger network is divided. Starting from a /24 and borrowing 2 host bits creates 4 subnets of /26 each. Each /26 holds 64 addresses, so the networks start at .0, .64, .128 and .192. The size of each block is called the increment, and it is 256 minus the last non-255 octet of the mask. For 255.255.255.192 the increment is 64.

Variable length subnet masking lets one address block be split into pieces of different sizes. A designer starts with the largest requirement, gives it the smallest block that fits, and then continues with the next largest. This wastes fewer addresses than using one mask everywhere.

Private address ranges are set aside for use inside organisations and are not routed on the public internet. They are 10.0.0.0/8, 172.16.0.0/12 and 192.168.0.0/16. Addresses in 169.254.0.0/16 are link-local and appear when a host expects DHCP but gets no answer.

# ARP: finding a hardware address

Ethernet delivers frames using MAC addresses, but applications and routers think in IP addresses. The Address Resolution Protocol connects the two on a single local network. When a host wants to send to an IP address on its own subnet, it needs the MAC address that goes with it.

The host first checks its ARP cache, a small table of recent IP-to-MAC mappings. If the entry is missing, it broadcasts an ARP request to the address ff:ff:ff:ff:ff:ff asking who has the target IP. Every device on the segment receives the broadcast, but only the owner of that IP replies. The reply is sent directly to the asker as a unicast frame and contains the owner's MAC address. The asker stores the mapping in its cache, and cache entries expire after a short time, often a few minutes, so stale data is discarded.

ARP only works inside one broadcast domain. If the destination is on a different subnet, the host does not ask for the destination's MAC address. It decides the destination is remote by comparing the address with its own subnet mask, and it sends the frame to the default gateway instead. So the host ARPs for the gateway's IP, and the frame carries the gateway's MAC address while the packet still carries the final destination IP. Each router along the path repeats this process on its own links.

A gratuitous ARP is a reply that nobody asked for. A host sends one to announce a new address, to update neighbours after a change of network card, or to detect an address conflict. Because ARP has no authentication, an attacker on the same network can send false replies and make victims send traffic to the wrong MAC address. This attack is called ARP spoofing or ARP poisoning, and switches with dynamic ARP inspection are one defence against it.

# DNS: names to addresses

The Domain Name System translates names such as www.example.com into IP addresses. It is a distributed, hierarchical database. At the top are the root servers, below them the top-level domains such as .com and .org, and below those the authoritative servers that hold the records for each domain.

A host does not usually walk this tree itself. It asks a recursive resolver, often run by the internet provider or a public service. The resolver checks its cache. If the answer is missing, it asks a root server, which points it to the servers for the top-level domain. Those point to the authoritative server for the domain, and that server returns the final answer. The resolver hands the answer to the host and keeps a copy until the record's time to live, or TTL, runs out. A short TTL lets changes spread quickly. A long TTL reduces load.

Different record types answer different questions. An A record maps a name to an IPv4 address and an AAAA record maps a name to an IPv6 address. A CNAME record makes one name an alias for another name, and it cannot sit beside other records for the same name. An MX record names the mail servers for a domain, each with a preference number where the lowest number is tried first. An NS record names the authoritative servers for a zone. A PTR record supports reverse lookups from an address back to a name. A TXT record holds free text, which is often used to prove domain ownership or to publish email policy.

DNS normally uses UDP on port 53 because queries and answers are small and a connection handshake would be wasteful. TCP on port 53 is used when a response is too large for a single UDP datagram and for zone transfers between servers. A negative answer is also cached, so a name that does not exist can be remembered as missing for a while.

Plain DNS is not encrypted, so anyone on the path can read the questions. DNS over HTTPS and DNS over TLS wrap the queries to hide them from the local network. DNSSEC is a separate feature that signs records so a resolver can detect forged answers, but it does not hide anything.

# NAT: sharing public addresses

Network Address Translation rewrites addresses in packet headers as they cross a router. It exists mainly because IPv4 addresses ran short. A home or office network uses private addresses inside, and the router presents a single public address to the internet.

The most common form is port address translation, also called NAT overload or masquerading. When an inside host at 192.168.1.20 opens a connection from source port 51000, the router replaces the source address with its own public address and usually picks a unique public source port, such as 40001. It records the pairing in a translation table. When the reply returns to the public address and port 40001, the router looks up the entry, rewrites the destination back to 192.168.1.20 and port 51000, and forwards the packet inside. The port number is what lets thousands of inside hosts share one public address.

Because the table entry is created by an outgoing packet, unsolicited inbound traffic has no matching entry and is dropped. This gives a side effect that looks like a firewall, but NAT was designed for address sharing, not for security. To let outside users reach a server inside, an administrator creates a static mapping called port forwarding, for example sending public port 443 to 192.168.1.50 port 443.

Static NAT maps one private address to one public address permanently. Dynamic NAT takes addresses from a pool as needed and releases them when idle. Both use up one public address per inside host, which is why overload is far more common.

NAT causes problems for protocols that carry addresses inside their payload, such as some voice and file transfer protocols, and for two hosts that both sit behind NAT and want to connect directly. Workarounds include application-layer gateways, STUN servers that help a host discover its public mapping, and relays. IPv6 has enough addresses for every device, so it removes the need for address sharing, although routers can still filter inbound traffic with a stateful firewall.
