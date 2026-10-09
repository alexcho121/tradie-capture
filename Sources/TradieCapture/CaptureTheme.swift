import SwiftUI

enum CaptureTheme {
    static let background = Color(red: 0.045, green: 0.047, blue: 0.043)
    static let surface = Color(red: 0.105, green: 0.110, blue: 0.098)
    static let yellow = Color(red: 1.0, green: 0.790, blue: 0.0)
    static let ink = Color(red: 0.045, green: 0.047, blue: 0.043)
    static let primaryText = Color.white
    static let secondaryText = Color.white.opacity(0.58)
    static let rule = Color.white.opacity(0.16)

    static let screenPadding: CGFloat = 24
    static let captureCornerRadius: CGFloat = 4
}
