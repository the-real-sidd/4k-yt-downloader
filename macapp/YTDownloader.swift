// 4K YT Downloader.app: shows the download page in its own window instead of a browser.
//
// The page (Resources/ytdl/ytdl_web.html) and the downloading itself come from
// Resources/ytdl/ytdl_web.py, which this app starts on 127.0.0.1 and stops when it quits.
// The app adds the native parts: the window, the menus, the folder panel, reading the
// clipboard for the Paste button (and for a YouTube link copied just before switching
// here) and the quit check.
// Built by build.sh.

import Cocoa
import WebKit

let appName = "4K YT Downloader"
let activeStatuses: Set<String> = ["starting", "downloading", "finishing", "converting"]
// Apps opened from Finder don't see Homebrew's folders, so look there first.
let toolFolders = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
let logFile = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/\(appName).log")

func findTool(_ name: String) -> String? {
    toolFolders.map { "\($0)/\(name)" }.first { FileManager.default.isExecutableFile(atPath: $0) }
}

// Matches the page background, so the window doesn't flash white while loading.
let pageBackground = NSColor(name: nil) { appearance in
    appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        ? NSColor(srgbRed: 0x12 / 255, green: 0x12 / 255, blue: 0x12 / 255, alpha: 1)
        : NSColor(srgbRed: 0xF5 / 255, green: 0xF4 / 255, blue: 0xF1 / 255, alpha: 1)
}

func statusPage(_ text: String) -> String {
    """
    <!doctype html><meta charset="utf-8"><style>
      :root { color-scheme: light dark; }
      body { margin: 0; height: 100vh; display: grid; place-items: center; background: transparent;
             font: 15px -apple-system, system-ui, sans-serif; color: #6E6C67; -webkit-user-select: none; }
      @media (prefers-color-scheme: dark) { body { color: #A09E98; } }
    </style><body>\(text)</body>
    """
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var webView: WKWebView!
    var server: Process?
    var port: Int?
    var pendingOutput = ""
    var log: FileHandle?
    var quitting = false
    var lastClipboardCheck = -1  // NSPasteboard changeCount at the last automatic look

    // MARK: Launch

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()
        guard let python = checkTools() else { return }
        startServer(python: python)
    }

    /// Coming to the front: let the page fill in a YouTube link that was just copied.
    func applicationDidBecomeActive(_ notification: Notification) {
        guard port != nil else { return }
        webView.evaluateJavaScript("window.ytdlAppActivated && window.ytdlAppActivated()", completionHandler: nil)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return true
    }

    func buildWindow() {
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "ytdl")
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        webView.loadHTMLString(statusPage("Starting…"), baseURL: nil)

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 780, height: 680),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered, defer: false)
        window.title = appName
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.backgroundColor = pageBackground
        window.minSize = NSSize(width: 520, height: 420)
        window.contentView = webView
        window.delegate = self
        window.center()
        window.setFrameAutosaveName("MainWindow")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Returns the python3 to use, or explains what to install and quits.
    func checkTools() -> String? {
        let needed = [("yt-dlp", "yt-dlp"), ("ffmpeg", "ffmpeg"), ("ffprobe", "ffmpeg"), ("deno", "deno"), ("python3", "python")]
        var missing: [String] = []
        for (tool, formula) in needed where findTool(tool) == nil && !missing.contains(formula) {
            missing.append(formula)
        }
        if missing.isEmpty, let python = findTool("python3") {
            return python
        }

        let command = "brew install " + missing.joined(separator: " ")
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "A few free tools are missing"
        alert.informativeText = """
            \(appName) uses yt-dlp and ffmpeg to download and convert videos. Open Terminal and run:

            \(command)

            No Homebrew yet? Install it first from brew.sh, then open \(appName) again.
            """
        alert.addButton(withTitle: "Copy Command")
        alert.addButton(withTitle: "Quit")
        if alert.runModal() == .alertFirstButtonReturn {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(command, forType: .string)
        }
        NSApp.terminate(nil)
        return nil
    }

    // MARK: Background server

    func startServer(python: String) {
        guard let script = Bundle.main.path(forResource: "ytdl_web", ofType: "py", inDirectory: "ytdl") else {
            fail("The app is damaged: its downloader files are missing. Download it again.")
            return
        }
        openLog()
        port = nil
        pendingOutput = ""

        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = [script, "--app", "--no-open", "--watch", String(getpid())]
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = (toolFolders + [environment["PATH"] ?? ""]).joined(separator: ":")
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"  // never write into the signed app
        process.environment = environment
        process.currentDirectoryURL = FileManager.default.homeDirectoryForCurrentUser

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                return
            }
            DispatchQueue.main.async { self?.received(data) }
        }
        process.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async { self?.serverStopped() }
        }

        do {
            try process.run()
        } catch {
            fail("Couldn't start the downloader: \(error.localizedDescription)")
            return
        }
        server = process

        DispatchQueue.main.asyncAfter(deadline: .now() + 20) { [weak self] in
            guard let self, self.port == nil, self.server?.isRunning == true else { return }
            self.fail("The downloader is taking too long to start.")
        }
    }

    func openLog() {
        guard log == nil else { return }
        try? FileManager.default.createDirectory(at: logFile.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: logFile.path) {
            FileManager.default.createFile(atPath: logFile.path, contents: nil)
        }
        log = try? FileHandle(forWritingTo: logFile)
        log?.seekToEndOfFile()
    }

    /// Server output goes to the log. Its "PORT <n>" line says where the page is.
    func received(_ data: Data) {
        log?.write(data)
        guard port == nil, let text = String(data: data, encoding: .utf8) else { return }
        pendingOutput += text
        let lines = pendingOutput.components(separatedBy: "\n")
        for line in lines.dropLast() where line.hasPrefix("PORT ") {
            if let value = Int(line.dropFirst(5).trimmingCharacters(in: .whitespaces)) {
                port = value
                pendingOutput = ""
                webView.load(URLRequest(url: URL(string: "http://127.0.0.1:\(value)/")!))
                return
            }
        }
        pendingOutput = lines.last ?? ""
    }

    func serverStopped() {
        guard !quitting else { return }
        server = nil
        port = nil
        webView.loadHTMLString(statusPage("The downloader stopped."), baseURL: nil)
        let alert = NSAlert()
        alert.messageText = "The downloader stopped"
        alert.informativeText = "Something went wrong in the background. Details are in ~/Library/Logs/\(appName).log."
        alert.addButton(withTitle: "Restart")
        alert.addButton(withTitle: "Quit")
        if alert.runModal() == .alertFirstButtonReturn, let python = findTool("python3") {
            webView.loadHTMLString(statusPage("Starting…"), baseURL: nil)
            startServer(python: python)
        } else {
            NSApp.terminate(nil)
        }
    }

    func fail(_ message: String) {
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "\(appName) can't start"
        alert.informativeText = "\(message)\n\nDetails are in ~/Library/Logs/\(appName).log."
        alert.addButton(withTitle: "Quit")
        alert.runModal()
        NSApp.terminate(nil)
    }

    // MARK: Page

    /// The page posts {action: "chooseFolder", current: path} when "Change" is clicked,
    /// and {action: "readClipboard", id, auto} for the Paste button and the automatic check.
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any] else { return }
        switch body["action"] as? String {
        case "chooseFolder":
            chooseFolder(current: body["current"] as? String)
        case "readClipboard":
            readClipboard(id: body["id"] as? Int ?? 0, auto: body["auto"] as? Bool ?? false)
        default:
            break
        }
    }

    // MARK: Clipboard

    /// Since macOS 15.4 the system may ask "Allow paste?" when an app reads the clipboard
    /// by itself. The automatic check therefore only reads when something new was copied
    /// and macOS says (without reading it) that it is probably a link.
    func readClipboard(id: Int, auto: Bool) {
        let pasteboard = NSPasteboard.general
        if auto {
            guard pasteboard.changeCount != lastClipboardCheck else { return sendClipboard(id: id, text: nil) }
            lastClipboardCheck = pasteboard.changeCount
            if #available(macOS 15.4, *) {
                if pasteboard.accessBehavior == .alwaysDeny { return sendClipboard(id: id, text: nil) }
                pasteboard.__detectPatterns(forPatterns: [__NSPasteboardDetectionPattern.__probableWebURL]) { [weak self] patterns, _ in
                    let isLink = patterns?.contains(__NSPasteboardDetectionPattern.__probableWebURL) == true
                    DispatchQueue.main.async {
                        self?.sendClipboard(id: id, text: isLink ? NSPasteboard.general.string(forType: .string) : nil)
                    }
                }
                return
            }
        }
        let text = pasteboard.string(forType: .string)
        var denied = false
        if #available(macOS 15.4, *) {
            denied = text == nil && pasteboard.accessBehavior == .alwaysDeny
        }
        sendClipboard(id: id, text: text, denied: denied)
    }

    func sendClipboard(id: Int, text: String?, denied: Bool = false) {
        var argument = "null"
        if let text, let data = try? JSONSerialization.data(withJSONObject: text, options: .fragmentsAllowed) {
            argument = String(decoding: data, as: UTF8.self)
        }
        webView.evaluateJavaScript("window.ytdlClipboard(\(id), \(argument), \(denied))", completionHandler: nil)
    }

    // MARK: Folder

    func chooseFolder(current: String?) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Choose"
        panel.message = "Save downloads to:"
        if let current, !current.isEmpty {
            panel.directoryURL = URL(fileURLWithPath: current, isDirectory: true)
        }
        panel.beginSheetModal(for: window) { [weak self] response in
            self?.sendFolder(response == .OK ? panel.url?.path : nil)
        }
    }

    func sendFolder(_ path: String?) {
        var argument = "null"
        if let path, let data = try? JSONSerialization.data(withJSONObject: path, options: .fragmentsAllowed) {
            argument = String(decoding: data, as: UTF8.self)
        }
        webView.evaluateJavaScript("window.ytdlFolderChosen(\(argument))", completionHandler: nil)
    }

    /// Only the local page loads in the window. Anything else opens in the default browser.
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { return decisionHandler(.cancel) }
        if url.scheme == "about" || (url.host == "127.0.0.1" && url.port == port) {
            return decisionHandler(.allow)
        }
        NSWorkspace.shared.open(url)
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url {
            NSWorkspace.shared.open(url)
        }
        return nil
    }

    @objc func reloadPage(_ sender: Any?) {
        if port != nil { webView.reload() }
    }

    // MARK: Quit

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        NSApp.terminate(nil)  // one window: closing it quits, after the downloads check
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let port, server?.isRunning == true else { return .terminateNow }
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/state")!)
        request.timeoutInterval = 2
        URLSession.shared.dataTask(with: request) { [weak self] data, _, _ in
            var running = 0
            if let data,
               let state = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let jobs = state["jobs"] as? [[String: Any]] {
                running = jobs.filter { activeStatuses.contains($0["status"] as? String ?? "") }.count
            }
            DispatchQueue.main.async { self?.confirmQuit(running: running) }
        }.resume()
        return .terminateLater
    }

    func confirmQuit(running: Int) {
        guard running > 0 else {
            NSApp.reply(toApplicationShouldTerminate: true)
            return
        }
        if window.isMiniaturized { window.deminiaturize(nil) }
        window.makeKeyAndOrderFront(nil)
        let alert = NSAlert()
        alert.messageText = running == 1 ? "A download is still running" : "\(running) downloads are still running"
        alert.informativeText = "Quitting stops them. Download the same link again later to pick up where it stopped."
        alert.addButton(withTitle: "Quit")
        alert.addButton(withTitle: "Keep Downloading")
        alert.beginSheetModal(for: window) { response in
            NSApp.reply(toApplicationShouldTerminate: response == .alertFirstButtonReturn)
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        server?.terminate()  // the server stops its downloads on the way out
    }

    // MARK: Menus (the Edit menu is what makes ⌘V paste work in the page)

    func buildMenu() {
        let main = NSMenu()

        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About \(appName)", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide \(appName)", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
            .keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit \(appName)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
            .keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")

        let viewMenu = NSMenu(title: "View")
        viewMenu.addItem(withTitle: "Reload Page", action: #selector(reloadPage(_:)), keyEquivalent: "r").target = self

        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        windowMenu.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")

        for menu in [appMenu, editMenu, viewMenu, windowMenu] {
            let item = NSMenuItem()
            item.submenu = menu
            main.addItem(item)
        }
        NSApp.mainMenu = main
        NSApp.windowsMenu = windowMenu
    }
}

MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.regular)
    app.run()
}
