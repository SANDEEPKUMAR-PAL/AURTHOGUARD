import Foundation
import IOBluetooth

setbuf(stdout, nil)
setbuf(stderr, nil)

final class BluetoothSPPBridge: NSObject, IOBluetoothRFCOMMChannelDelegate {
    private let targetName: String
    private var device: IOBluetoothDevice?
    private var channel: IOBluetoothRFCOMMChannel?
    private var rxBuffer = Data()
    private var isOpen = false

    init(targetName: String) {
        self.targetName = targetName
        super.init()
    }

    func start() -> Never {
        guard let matched = findPairedDevice() else {
            fputs("[macos_bt_spp] No paired Bluetooth device matching '\(targetName)' found.\n", stderr)
            exit(2)
        }
        self.device = matched
        let devName = matched.name ?? targetName
        let devAddr = matched.addressString ?? "unknown"
        fputs("[macos_bt_spp] Connecting to \(devName) (\(devAddr))...\n", stderr)

        matched.performSDPQuery(nil)
        RunLoop.current.run(until: Date(timeIntervalSinceNow: 1.0))

        var channelID: BluetoothRFCOMMChannelID = 1
        if let services = matched.services as? [IOBluetoothSDPServiceRecord] {
            for svc in services {
                var cid: BluetoothRFCOMMChannelID = 0
                if svc.getRFCOMMChannelID(&cid) == kIOReturnSuccess && cid > 0 {
                    channelID = cid
                    break
                }
            }
        }

        var openedChannel: IOBluetoothRFCOMMChannel?
        let rc = matched.openRFCOMMChannelAsync(&openedChannel, withChannelID: channelID, delegate: self)
        if rc != kIOReturnSuccess {
            fputs("[macos_bt_spp] Failed to initiate RFCOMM open (code \(rc)).\n", stderr)
            exit(3)
        }
        self.channel = openedChannel

        DispatchQueue.main.asyncAfter(deadline: .now() + 8.0) { [weak self] in
            guard let self = self, !self.isOpen else { return }
            fputs("[macos_bt_spp] Connection timed out.\n", stderr)
            self.shutdownAndExit(code: 4)
        }

        startStdinReader()
        RunLoop.current.run()
        exit(0)
    }

    private func findPairedDevice() -> IOBluetoothDevice? {
        guard let paired = IOBluetoothDevice.pairedDevices() as? [IOBluetoothDevice] else {
            return nil
        }
        for dev in paired {
            if let name = dev.name, name.localizedCaseInsensitiveContains(targetName) {
                return dev
            }
        }
        return nil
    }

    private func startStdinReader() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            while let line = readLine(strippingNewline: false) {
                guard let self = self else { break }
                let bytes = Array(line.utf8)
                guard !bytes.isEmpty else { continue }
                DispatchQueue.main.async {
                    guard self.isOpen, let ch = self.channel, ch.isOpen() else { return }
                    var mutable = bytes
                    let maxLen = Int(UInt16.max)
                    var offset = 0
                    while offset < mutable.count {
                        let chunkLen = min(maxLen, mutable.count - offset)
                        mutable.withUnsafeMutableBytes { rawBuf in
                            if let base = rawBuf.baseAddress {
                                _ = ch.writeSync(base.advanced(by: offset), length: UInt16(chunkLen))
                            }
                        }
                        offset += chunkLen
                    }
                }
            }
            DispatchQueue.main.async {
                self?.shutdownAndExit(code: 0)
            }
        }
    }

    func rfcommChannelOpenComplete(_ rfcommChannel: IOBluetoothRFCOMMChannel!, status error: IOReturn) {
        if error != kIOReturnSuccess {
            fputs("[macos_bt_spp] RFCOMM open failed (status \(error)).\n", stderr)
            shutdownAndExit(code: 5)
            return
        }
        self.channel = rfcommChannel
        self.isOpen = true
        fputs("[macos_bt_spp] Connected RFCOMM channel \(rfcommChannel?.getID() ?? 1).\n", stderr)
    }

    func rfcommChannelData(_ rfcommChannel: IOBluetoothRFCOMMChannel!, data dataPointer: UnsafeMutableRawPointer!, length dataLength: Int) {
        guard dataLength > 0, let ptr = dataPointer else { return }
        rxBuffer.append(Data(bytes: ptr, count: dataLength))
        while let nlRange = rxBuffer.firstRange(of: Data([0x0A])) {
            let lineData = Data(rxBuffer[..<nlRange.lowerBound])
            rxBuffer = Data(rxBuffer[nlRange.upperBound...])
            if var text = String(data: lineData, encoding: .utf8) {
                text = text.trimmingCharacters(in: CharacterSet(charactersIn: "\r"))
                if !text.isEmpty {
                    let out = (text + "\n")
                    if let outData = out.data(using: .utf8) {
                        FileHandle.standardOutput.write(outData)
                    }
                }
            }
        }
    }

    func rfcommChannelClosed(_ rfcommChannel: IOBluetoothRFCOMMChannel!) {
        fputs("[macos_bt_spp] RFCOMM channel closed.\n", stderr)
        shutdownAndExit(code: 1)
    }

    private func shutdownAndExit(code: Int32) -> Never {
        if let ch = channel, ch.isOpen() {
            ch.close()
        }
        if isOpen {
            device?.closeConnection()
        }
        exit(code)
    }
}

let target = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "OA-Wearable"
let bridge = BluetoothSPPBridge(targetName: target)
bridge.start()
