import Photos
import SwiftUI
import UIKit
import WebKit

/// Shows the X Media AI Video web page from the computer, full screen.
struct WebScreen: View {
    let url: URL
    @ObservedObject var state: AppState
    @StateObject private var model = WebModel()

    var body: some View {
        ZStack {
            WebView(url: url, model: model)
                .ignoresSafeArea(edges: .bottom)

            if let err = model.loadError {
                VStack(spacing: 14) {
                    Text("📡").font(.system(size: 48))
                    Text("ເຊື່ອມຕໍ່ຄອມພິວເຕີບໍ່ໄດ້").font(.title3.bold())
                    Text(Server.display(url)).font(.system(.footnote, design: .monospaced)).foregroundColor(Brand.muted)
                    Text(err).font(.footnote).foregroundColor(Brand.muted).multilineTextAlignment(.center)
                    Text("ກວດວ່າເປີດ start.bat ໃນຄອມພິວເຕີ ແລະ iPhone ຕໍ່ Wi-Fi ດຽວກັນ")
                        .font(.footnote).multilineTextAlignment(.center)
                    BrandButton(title: "ລອງໃໝ່ · Retry") { model.reload() }
                    BrandButton(title: "ປ່ຽນທີ່ຢູ່ / ຄົ້ນຫາ · Change", yellow: true) { state.showSetup = true }
                }
                .padding(28)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color.black)
            }

            if model.saving {
                VStack(spacing: 12) {
                    ProgressView().tint(Brand.orange).scaleEffect(1.4)
                    Text("ກຳລັງບັນທຶກລົງ Photos...").font(.subheadline)
                }
                .padding(24)
                .background(Brand.panel)
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            }

            if let t = model.toast {
                VStack {
                    Spacer()
                    Text(t)
                        .font(.subheadline.bold())
                        .padding(.horizontal, 18).padding(.vertical, 12)
                        .background(Brand.panel)
                        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Brand.orange))
                        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                        .padding(.bottom, 48)
                }
                .transition(.opacity)
            }
        }
        .onAppear { model.onChangeServer = { state.showSetup = true } }
    }
}

struct WebView: UIViewRepresentable {
    let url: URL
    let model: WebModel

    func makeUIView(context: Context) -> WKWebView {
        model.makeWebView(url: url)
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {
        if model.url != url {
            model.url = url
            uiView.load(URLRequest(url: url))
        }
    }
}

/// Avoids a retain cycle between WKUserContentController and the model.
final class WeakScriptHandler: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?
    init(_ target: WKScriptMessageHandler) { self.target = target }
    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        target?.userContentController(c, didReceive: m)
    }
}

enum SaveError: LocalizedError {
    case denied, badResponse
    var errorDescription: String? {
        switch self {
        case .denied: return "ບໍ່ມີສິດບັນທຶກລົງ Photos (Settings → X AI Video → Photos)"
        case .badResponse: return "ດາວໂຫຼດວິດີໂອບໍ່ໄດ້"
        }
    }
}

final class WebModel: NSObject, ObservableObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    @Published var loadError: String? = nil
    @Published var saving = false
    @Published var toast: String? = nil
    var onChangeServer: (() -> Void)? = nil
    var url: URL? = nil
    private var webView: WKWebView? = nil

    func makeWebView(url: URL) -> WKWebView {
        if let wv = webView { return wv }
        self.url = url
        let cfg = WKWebViewConfiguration()
        cfg.allowsInlineMediaPlayback = true
        cfg.mediaTypesRequiringUserActionForPlayback = []
        let ucc = WKUserContentController()
        let js = "window.XAV_IOS = true; document.documentElement.classList.add('ios-app');"
        ucc.addUserScript(WKUserScript(source: js, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        ucc.add(WeakScriptHandler(self), name: "xav")
        cfg.userContentController = ucc

        let wv = WKWebView(frame: .zero, configuration: cfg)
        wv.navigationDelegate = self
        wv.uiDelegate = self
        wv.isOpaque = false
        wv.backgroundColor = .black
        wv.scrollView.backgroundColor = .black
        let refresh = UIRefreshControl()
        refresh.tintColor = UIColor(red: 1, green: 0.48, blue: 0, alpha: 1)
        refresh.addTarget(self, action: #selector(pullToRefresh(_:)), for: .valueChanged)
        wv.scrollView.refreshControl = refresh
        webView = wv
        wv.load(URLRequest(url: url))
        return wv
    }

    func reload() {
        loadError = nil
        if let url = url { webView?.load(URLRequest(url: url)) }
    }

    @objc private func pullToRefresh(_ sender: UIRefreshControl) {
        if webView?.url == nil { reload() } else { webView?.reload() }
    }

    private static func isDownload(_ u: URL) -> Bool {
        return (u.query ?? "").contains("download=true")
    }

    // MARK: navigation
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let u = navigationAction.request.url, WebModel.isDownload(u) {
            decisionHandler(.cancel)
            saveToPhotos(u)
            return
        }
        if navigationAction.targetFrame == nil, let u = navigationAction.request.url {
            decisionHandler(.cancel)
            webView.load(URLRequest(url: u))
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let u = navigationAction.request.url {
            if WebModel.isDownload(u) { saveToPhotos(u) } else { webView.load(navigationAction.request) }
        }
        return nil
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loadError = nil
        webView.scrollView.refreshControl?.endRefreshing()
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        handle(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        handle(error)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        webView.reload()
    }

    private func handle(_ error: Error) {
        webView?.scrollView.refreshControl?.endRefreshing()
        let ns = error as NSError
        if ns.domain == NSURLErrorDomain && ns.code == NSURLErrorCancelled { return }
        if ns.domain == "WebKitErrorDomain" && ns.code == 102 { return }  // interrupted by our download handling
        loadError = ns.localizedDescription
    }

    // MARK: messages from the page
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? String else { return }
        switch body {
        case "changeServer": onChangeServer?()
        case "reload": reload()
        default: break
        }
    }

    // MARK: save finished video to Photos
    func saveToPhotos(_ url: URL) {
        if saving { return }
        saving = true
        Task {
            do {
                let (tmp, resp) = try await URLSession.shared.download(from: url)
                guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else { throw SaveError.badResponse }
                let dest = FileManager.default.temporaryDirectory
                    .appendingPathComponent(UUID().uuidString)
                    .appendingPathExtension("mp4")
                try FileManager.default.moveItem(at: tmp, to: dest)
                let status = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
                guard status == .authorized || status == .limited else { throw SaveError.denied }
                try await PHPhotoLibrary.shared().performChanges {
                    _ = PHAssetChangeRequest.creationRequestForAssetFromVideo(atFileURL: dest)
                }
                try? FileManager.default.removeItem(at: dest)
                await MainActor.run {
                    self.saving = false
                    self.showToast("✓ ບັນທຶກລົງ Photos ແລ້ວ")
                }
            } catch {
                await MainActor.run {
                    self.saving = false
                    self.showToast("ບັນທຶກບໍ່ສຳເລັດ: \(error.localizedDescription)")
                }
            }
        }
    }

    private func showToast(_ text: String) {
        withAnimation { toast = text }
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
            withAnimation { self?.toast = nil }
        }
    }
}
