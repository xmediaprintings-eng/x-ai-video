import SwiftUI

/// First screen: connect to the computer that runs start.bat.
struct SetupView: View {
    @ObservedObject var state: AppState
    @State private var address = ""
    @State private var busy = false
    @State private var message: String? = nil

    var body: some View {
        ScrollView {
            VStack(spacing: 18) {
                LogoBox(size: 84).padding(.top, 36)
                VStack(spacing: 4) {
                    Text("X Media AI Video").font(.system(size: 26, weight: .heavy))
                    Text("ເຊື່ອມຕໍ່ກັບໂປຣແກຣມໃນຄອມພິວເຕີ").foregroundColor(Brand.muted)
                }

                VStack(alignment: .leading, spacing: 8) {
                    Text("ທີ່ຢູ່ຄອມພິວເຕີ · Server address")
                        .font(.subheadline).foregroundColor(Brand.muted)
                    TextField("192.168.1.25:8000", text: $address)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .disableAutocorrection(true)
                        .font(.system(.body, design: .monospaced))
                        .padding(12)
                        .background(Brand.panel)
                        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(Brand.line))
                }

                BrandButton(title: "ເຊື່ອມຕໍ່ · Connect", action: connect)
                    .disabled(busy)
                BrandButton(title: "🔍 ຄົ້ນຫາອັດຕະໂນມັດ · Auto-find", yellow: true, action: autoFind)
                    .disabled(busy)

                if busy { ProgressView().tint(Brand.orange).padding(.top, 4) }
                if let message = message {
                    Text(message)
                        .font(.footnote)
                        .foregroundColor(Color(hex: 0xffb38a))
                        .multilineTextAlignment(.center)
                }

                VStack(alignment: .leading, spacing: 6) {
                    Text("ວິທີໃຊ້ · How to")
                        .font(.subheadline.bold())
                    Text("1. ເປີດ start.bat ໃນຄອມພິວເຕີ")
                    Text("2. iPhone ຕໍ່ Wi-Fi ດຽວກັນກັບຄອມພິວເຕີ")
                    Text("3. ກົດ ຄົ້ນຫາອັດຕະໂນມັດ ຫຼື ພິມທີ່ຢູ່ ຈາກແຖວ \"On your phone\"")
                    Text("4. ຖ້າ iPhone ຖາມສິດ Local Network ໃຫ້ກົດ Allow")
                }
                .font(.footnote)
                .foregroundColor(Brand.muted)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(14)
                .background(Brand.panel)
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))

                if state.serverURL != nil {
                    Button("ຍົກເລີກ · Cancel") { state.showSetup = false }
                        .foregroundColor(Brand.muted)
                }
            }
            .padding(24)
        }
        .onAppear {
            if address.isEmpty { address = Server.display(state.serverURL) }
        }
    }

    private func connect() {
        guard let url = Server.normalize(address) else {
            message = "ທີ່ຢູ່ບໍ່ຖືກຕ້ອງ · Invalid address"
            return
        }
        busy = true
        message = nil
        Task {
            let ok = await Server.check(url)
            await MainActor.run {
                busy = false
                if ok {
                    state.serverURL = url
                    state.showSetup = false
                } else {
                    message = "ເຊື່ອມຕໍ່ບໍ່ໄດ້ — ກວດວ່າເປີດ start.bat ແລ້ວ ແລະ ຕໍ່ Wi-Fi ດຽວກັນ. ຖ້າ iPhone ຫາກໍຖາມສິດ Local Network ໃຫ້ກົດ Allow ແລ້ວລອງໃໝ່."
                }
            }
        }
    }

    private func autoFind() {
        busy = true
        message = "ກຳລັງຄົ້ນຫາ... Searching your Wi-Fi"
        Task {
            // first request triggers the Local Network permission prompt; try twice
            var found = await Server.scan()
            if found == nil {
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                found = await Server.scan()
            }
            await MainActor.run {
                busy = false
                if let url = found {
                    address = Server.display(url)
                    message = nil
                    state.serverURL = url
                    state.showSetup = false
                } else {
                    message = "ບໍ່ພົບຄອມພິວເຕີ — ກວດວ່າເປີດ start.bat ແລ້ວ ແລະ ຕໍ່ Wi-Fi ດຽວກັນ, ຫຼື ພິມທີ່ຢູ່ເອງ."
                }
            }
        }
    }
}
