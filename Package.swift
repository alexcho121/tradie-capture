// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "TradieCapture",
    platforms: [
        .iOS(.v17),
        // Lets `swift build` validate the shared SwiftUI source on this Mac.
        .macOS(.v14)
    ],
    products: [
        .executable(name: "TradieCapture", targets: ["TradieCapture"])
    ],
    targets: [
        .executableTarget(name: "TradieCapture")
    ]
)
