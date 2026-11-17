import SwiftUI

@main
struct XAIVideoApp: App {
    @StateObject private var state = AppState()

    var body: some Scene {
        WindowGroup {
            RootView(state: state)
                .preferredColorScheme(.dark)
        }
    }
}

/// Remembers which computer (server) the app talks to.
final class AppState: ObservableObject {
    @Published var serverURL: URL? {
        didSet { UserDefaults.standard.set(serverURL?.absoluteString, forKey: "serverURL") }
    }
    @Published var showSetup: Bool

    init() {
        var saved: URL? = nil
        if let s = UserDefaults.standard.string(forKey: "serverURL") {
            saved = URL(string: s)
        }
        serverURL = saved
        showSetup = (saved == nil)
    }
}

struct RootView: View {
    @ObservedObject var state: AppState

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if let url = state.serverURL, !state.showSetup {
                WebScreen(url: url, state: state)
            } else {
                SetupView(state: state)
            }
        }
    }
}

// MARK: - Brand

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xff) / 255.0,
                  green: Double((hex >> 8) & 0xff) / 255.0,
                  blue: Double(hex & 0xff) / 255.0)
    }
}

enum Brand {
    static let yellow = Color(hex: 0xffc400)
    static let orange = Color(hex: 0xff7a00)
    static let red = Color(hex: 0xe8231b)
    static let ink = Color(hex: 0x120800)
    static let panel = Color(hex: 0x121110)
    static let line = Color(hex: 0x2f2a24)
    static let muted = Color(hex: 0xa8998a)
    static let gradient = LinearGradient(colors: [yellow, orange, red], startPoint: .leading, endPoint: .trailing)
}

struct LogoBox: View {
    var size: CGFloat

    var body: some View {
        Text("X")
            .font(.system(size: size * 0.66, weight: .black))
            .foregroundColor(Brand.ink)
            .frame(width: size, height: size)
            .background(Brand.gradient)
            .clipShape(RoundedRectangle(cornerRadius: size * 0.22, style: .continuous))
            .shadow(color: Brand.orange.opacity(0.45), radius: 18)
    }
}

struct BrandButton: View {
    let title: String
    var yellow: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.headline)
                .foregroundColor(.black)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .background(yellow ? AnyView(Brand.yellow) : AnyView(Brand.gradient))
                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        }
    }
}
