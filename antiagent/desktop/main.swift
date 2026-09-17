import Cocoa
import WebKit

class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var daemonProcess: Process?

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupMenuBar()
        ensureBackendRunning()

        let windowRect = NSRect(x: 0, y: 0, width: 1160, height: 800)
        window = NSWindow(
            contentRect: windowRect,
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "AntiAgent Guard"
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.backgroundColor = NSColor(red: 13/255.0, green: 17/255.0, blue: 23/255.0, alpha: 1.0)
        window.minSize = NSSize(width: 800, height: 600)
        window.center()
        window.setFrameAutosaveName("AntiAgentMainWindow")
        window.delegate = self

        let config = WKWebViewConfiguration()
        webView = WKWebView(frame: window.contentView!.bounds, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")

        window.contentView?.addSubview(webView)

        if let url = URL(string: "http://127.0.0.1:4242") {
            let request = URLRequest(url: url)
            webView.load(request)
        }

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        // Automatically retry connecting if the local daemon is still initializing
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
            if let url = URL(string: "http://127.0.0.1:4242") {
                webView.load(URLRequest(url: url))
            }
        }
    }

    func ensureBackendRunning() {
        let url = URL(string: "http://127.0.0.1:4242/api/status")!
        var isUp = false
        let sema = DispatchSemaphore(value: 0)
        let task = URLSession.shared.dataTask(with: url) { _, resp, _ in
            if let http = resp as? HTTPURLResponse, http.statusCode == 200 {
                isUp = true
            }
            sema.signal()
        }
        task.resume()
        _ = sema.wait(timeout: .now() + 0.4)

        if !isUp {
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            proc.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser

            var env = ProcessInfo.processInfo.environment
            let extraPaths = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
            env["PATH"] = extraPaths + ":" + (env["PATH"] ?? "")

            if let resPath = Bundle.main.resourcePath {
                let existingPythonPath = env["PYTHONPATH"] ?? ""
                env["PYTHONPATH"] = existingPythonPath.isEmpty ? resPath : "\(resPath):\(existingPythonPath)"
            }
            proc.environment = env
            proc.arguments = ["python3", "-m", "antiagent.dashboard", "--no-open"]
            try? proc.run()
            daemonProcess = proc

            // Poll briefly until the backend starts
            for _ in 0..<15 {
                Thread.sleep(forTimeInterval: 0.1)
                var ready = false
                let checkSema = DispatchSemaphore(value: 0)
                let checkTask = URLSession.shared.dataTask(with: url) { _, resp, _ in
                    if let http = resp as? HTTPURLResponse, http.statusCode == 200 {
                        ready = true
                    }
                    checkSema.signal()
                }
                checkTask.resume()
                _ = checkSema.wait(timeout: .now() + 0.1)
                if ready { break }
            }
        }
    }

    func setupMenuBar() {
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)

        let appMenu = NSMenu()
        appMenuItem.submenu = appMenu

        appMenu.addItem(withTitle: "About AntiAgent", action: nil, keyEquivalent: "")
        appMenu.addItem(withTitle: "Check for Updates...", action: #selector(checkForUpdates), keyEquivalent: "u")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Reload Dashboard", action: #selector(reloadDashboard), keyEquivalent: "r")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Quit AntiAgent", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "Edit")
        editMenuItem.submenu = editMenu
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "Cut", action: Selector(("cut:")), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: Selector(("copy:")), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: Selector(("paste:")), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: Selector(("selectAll:")), keyEquivalent: "a")

        NSApp.mainMenu = mainMenu
    }

    @objc func checkForUpdates() {
        webView.evaluateJavaScript("if (typeof openUpdateModal === 'function') { openUpdateModal(true); }", completionHandler: nil)
    }

    @objc func reloadDashboard() {
        webView.reload()
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        NSApp.terminate(nil)
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        daemonProcess?.terminate()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
