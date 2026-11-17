import Foundation

/// Finding and checking the X Media AI Video server running on the computer.
enum Server {
    static let defaultPort = 8000

    /// "192.168.1.25", "192.168.1.25:8000" or "http://..." -> http://192.168.1.25:8000/
    static func normalize(_ raw: String) -> URL? {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return nil }
        let lower = s.lowercased()
        if !lower.hasPrefix("http://") && !lower.hasPrefix("https://") {
            s = "http://" + s
        }
        guard var comps = URLComponents(string: s), let host = comps.host, !host.isEmpty else { return nil }
        if comps.port == nil { comps.port = defaultPort }
        comps.path = "/"
        comps.query = nil
        comps.fragment = nil
        return comps.url
    }

    static func display(_ url: URL?) -> String {
        guard let url = url, let host = url.host else { return "" }
        return "\(host):\(url.port ?? defaultPort)"
    }

    /// True if an X Media AI Video server answers at this address.
    static func check(_ base: URL, timeout: TimeInterval = 3) async -> Bool {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = timeout
        cfg.timeoutIntervalForResource = timeout
        let session = URLSession(configuration: cfg)
        defer { session.finishTasksAndInvalidate() }
        var req = URLRequest(url: base.appendingPathComponent("api/status"))
        req.timeoutInterval = timeout
        do {
            let (data, resp) = try await session.data(for: req)
            guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else { return false }
            let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            return obj?["ffmpeg"] != nil
        } catch {
            return false
        }
    }

    /// The iPhone's own Wi-Fi IPv4 address (interface en0).
    static func wifiIPv4() -> String? {
        var result: String? = nil
        var ifaddr: UnsafeMutablePointer<ifaddrs>? = nil
        guard getifaddrs(&ifaddr) == 0, let first = ifaddr else { return nil }
        defer { freeifaddrs(ifaddr) }
        var ptr: UnsafeMutablePointer<ifaddrs>? = first
        while let p = ptr {
            let iface = p.pointee
            if let sa = iface.ifa_addr, sa.pointee.sa_family == UInt8(AF_INET),
               String(cString: iface.ifa_name) == "en0" {
                var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
                if getnameinfo(sa, socklen_t(sa.pointee.sa_len), &host, socklen_t(host.count),
                               nil, 0, NI_NUMERICHOST) == 0 {
                    result = String(cString: host)
                }
            }
            ptr = iface.ifa_next
        }
        return result
    }

    /// Scan the local /24 network for a computer running the server.
    static func scan(port: Int = defaultPort) async -> URL? {
        guard let ip = wifiIPv4() else { return nil }
        let parts = ip.split(separator: ".").map(String.init)
        guard parts.count == 4 else { return nil }
        let prefix = parts[0...2].joined(separator: ".")
        let hosts = (1...254).map { "\(prefix).\($0)" }.filter { $0 != ip }
        let batchSize = 48
        var start = 0
        while start < hosts.count {
            let batch = Array(hosts[start..<min(start + batchSize, hosts.count)])
            start += batchSize
            let found = await withTaskGroup(of: URL?.self, returning: URL?.self) { group in
                for h in batch {
                    group.addTask {
                        guard let u = URL(string: "http://\(h):\(port)/") else { return nil }
                        return await Server.check(u, timeout: 1.2) ? u : nil
                    }
                }
                for await r in group {
                    if let r = r {
                        group.cancelAll()
                        return r
                    }
                }
                return nil
            }
            if let found = found { return found }
        }
        return nil
    }
}
