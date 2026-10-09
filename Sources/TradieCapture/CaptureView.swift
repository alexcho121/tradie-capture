import SwiftUI

enum CaptureState: Equatable {
    case ready
    case recording
    case countdown(Int)
    case saved
}

struct CaptureView: View {
    @State private var state: CaptureState = .ready
    @State private var elapsedSeconds = 0
    @State private var timerTask: Task<Void, Never>?

    var body: some View {
        ZStack {
            CaptureTheme.background.ignoresSafeArea()

            switch state {
            case .ready:
                readyView
            case .recording:
                recordingView
            case let .countdown(value):
                countdownView(value: value)
            case .saved:
                savedView
            }
        }
        .preferredColorScheme(.dark)
        .onDisappear { cancelScheduledWork() }
    }

    private var readyView: some View {
        VStack(alignment: .leading, spacing: 0) {
            projectContext

            Spacer(minLength: 28)

            Button(action: startRecording) {
                captureTarget(
                    title: "TAP TO\nCAPTURE",
                    detail: "Record now. Review later.",
                    isRecording: false
                )
            }
            .buttonStyle(CaptureButtonStyle())
            .accessibilityLabel("Tap to capture")
            .accessibilityHint("Starts a new field capture")

            Spacer(minLength: 28)

            reviewLink
        }
        .padding(CaptureTheme.screenPadding)
    }

    private var recordingView: some View {
        VStack(alignment: .leading, spacing: 0) {
            projectContext

            Spacer(minLength: 28)

            Button(action: finishRecording) {
                captureTarget(
                    title: "RECORDING",
                    detail: "TAP TO FINISH",
                    isRecording: true
                ) {
                    Text(timeString)
                        .font(.system(size: 64, weight: .bold, design: .monospaced))
                        .monospacedDigit()
                        .foregroundStyle(CaptureTheme.ink)
                        .minimumScaleFactor(0.6)
                }
            }
            .buttonStyle(CaptureButtonStyle())
            .accessibilityLabel("Recording, \(timeString)")
            .accessibilityHint("Tap to finish recording")

            Spacer(minLength: 20)

            Text("Say what changed, who asked, and any extra time.")
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(CaptureTheme.secondaryText)
                .frame(maxWidth: .infinity, alignment: .center)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 18)
        }
        .padding(CaptureTheme.screenPadding)
    }

    private func countdownView(value: Int) -> some View {
        VStack(spacing: 18) {
            Text("PHOTO IN")
                .font(.system(size: 22, weight: .heavy, design: .default))
                .tracking(1.6)
                .foregroundStyle(CaptureTheme.yellow)

            Text("\(value)")
                .font(.system(size: 180, weight: .black, design: .default))
                .monospacedDigit()
                .foregroundStyle(CaptureTheme.primaryText)
                .contentTransition(.numericText())
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Photo in \(value)")
    }

    private var savedView: some View {
        VStack(spacing: 20) {
            Text("✓")
                .font(.system(size: 92, weight: .bold))
                .foregroundStyle(CaptureTheme.yellow)

            Text("SAVED")
                .font(.system(size: 44, weight: .black, design: .default))
                .tracking(1.4)
                .foregroundStyle(CaptureTheme.primaryText)

            VStack(spacing: 3) {
                Text("10:43 AM · ABC Apartments")
                Text("Level 2")
            }
            .font(.system(size: 16, weight: .semibold))
            .foregroundStyle(CaptureTheme.secondaryText)
            .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Saved. 10:43 AM, ABC Apartments, Level 2")
    }

    private var projectContext: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text("ABC Apartments")
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(CaptureTheme.primaryText)
            Text("Level 2")
                .font(.system(size: 14, weight: .medium))
                .foregroundStyle(CaptureTheme.secondaryText)
        }
        .accessibilityElement(children: .combine)
    }

    private var reviewLink: some View {
        Text("2 records need review  →")
            .font(.system(size: 15, weight: .semibold))
            .foregroundStyle(CaptureTheme.secondaryText)
            .accessibilityLabel("2 records need review")
    }

    private func captureTarget<Center: View>(
        title: String,
        detail: String,
        isRecording: Bool,
        @ViewBuilder center: () -> Center = { EmptyView() }
    ) -> some View {
        VStack(spacing: 0) {
            HStack(spacing: 9) {
                if isRecording {
                    Circle()
                        .fill(CaptureTheme.ink)
                        .frame(width: 13, height: 13)
                }
                Text(title)
                    .font(.system(size: isRecording ? 26 : 37, weight: .black, design: .default))
                    .tracking(isRecording ? 1.3 : 0.4)
                    .multilineTextAlignment(.center)
                    .lineSpacing(-2)
            }
            .foregroundStyle(CaptureTheme.ink)

            center()
                .padding(.top, isRecording ? 16 : 0)

            Text(detail)
                .font(.system(size: 16, weight: .bold))
                .tracking(0.7)
                .foregroundStyle(CaptureTheme.ink.opacity(0.82))
                .padding(.top, isRecording ? 22 : 12)
        }
        .frame(maxWidth: .infinity, minHeight: 365)
        .padding(24)
        .background(CaptureTheme.yellow)
        .clipShape(RoundedRectangle(cornerRadius: CaptureTheme.captureCornerRadius, style: .continuous))
    }

    private var timeString: String {
        String(format: "00:%02d", elapsedSeconds)
    }

    private func startRecording() {
        cancelScheduledWork()
        elapsedSeconds = 0
        state = .recording

        timerTask = Task {
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                guard !Task.isCancelled else { return }
                elapsedSeconds += 1
            }
        }
    }

    private func finishRecording() {
        cancelScheduledWork()
        state = .countdown(3)

        timerTask = Task {
            for value in stride(from: 2, through: 1, by: -1) {
                try? await Task.sleep(for: .seconds(1))
                guard !Task.isCancelled else { return }
                state = .countdown(value)
            }

            try? await Task.sleep(for: .seconds(1))
            guard !Task.isCancelled else { return }
            state = .saved

            try? await Task.sleep(for: .milliseconds(1_500))
            guard !Task.isCancelled else { return }
            state = .ready
        }
    }

    private func cancelScheduledWork() {
        timerTask?.cancel()
        timerTask = nil
    }
}

private struct CaptureButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.78 : 1)
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
            .animation(.easeOut(duration: 0.08), value: configuration.isPressed)
    }
}

struct CaptureView_Previews: PreviewProvider {
    static var previews: some View {
        CaptureView()
    }
}
