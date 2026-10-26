import Foundation

enum APIError: LocalizedError {
    case invalidURL
    case invalidResponse
    case httpStatus(Int, String?)
    case unauthorized
    case decoding(Error)
    case network(Error)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            "Invalid API URL."
        case .invalidResponse:
            "Unexpected server response."
        case .unauthorized:
            "Your session has expired. Sign in again."
        case let .httpStatus(code, message):
            Self.userFacingHTTPMessage(statusCode: code, body: message)
        case let .decoding(error):
            "Could not read server response: \(error.localizedDescription)"
        case let .network(error):
            Self.networkMessage(for: error)
        }
    }

    private static func userFacingHTTPMessage(statusCode: Int, body: String?) -> String {
        let host = AppConfig.apiBaseURL.host ?? "the API"

        if let body, !body.isEmpty {
            if let data = body.data(using: .utf8),
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                if let error = json["error"] as? [String: Any],
                   let code = error["code"] as? String,
                   let message = error["message"] as? String,
                   !message.isEmpty {
                    if code == "provider_unavailable" {
                        return messageWithRequestID(message, error: error)
                    }
                    // TV-103: a retryable 503 whose message says what to do ("TV details are
                    // unavailable right now"); not a server outage.
                    if code == "tv_details_unavailable" {
                        return message
                    }
                    if code == "validation_error",
                       let fields = error["fields"] as? [String: Any],
                       let validationMessage = validationMessage(from: fields) {
                        return validationMessage
                    }
                    if (400..<500).contains(statusCode) {
                        return message
                    }
                }

                if let errors = json["errors"] as? [[String: Any]],
                   let first = errors.first {
                    let detail = (first["detail"] as? String) ?? (first["message"] as? String) ?? ""
                    let code = first["code"] as? Int
                    if detail.contains("Cloudflare Tunnel") || code == 1033 || statusCode == 530 {
                        return tunnelUnavailableMessage(host: host)
                    }
                    if (400..<500).contains(statusCode), !detail.isEmpty {
                        return detail
                    }
                }

                if (400..<500).contains(statusCode),
                   let validationMessage = validationMessage(from: json) {
                    return validationMessage
                }
            }

            let trimmed = body.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.contains("error code: 1033") || trimmed == "error code: 1033" {
                return tunnelUnavailableMessage(host: host)
            }

            if trimmed.contains("<html") || trimmed.contains("<!DOCTYPE") {
                return serverUnavailableMessage(host: host, statusCode: statusCode)
            }

            if (400..<500).contains(statusCode),
               trimmed.count <= 180,
               !trimmed.hasPrefix("{") {
                return trimmed
            }
        }

        if statusCode == 502 || statusCode == 503 || statusCode == 530 {
            return tunnelUnavailableMessage(host: host)
        }

        return "Request to \(host) failed (HTTP \(statusCode))."
    }

    private static func messageWithRequestID(_ message: String, error: [String: Any]) -> String {
        guard let requestID = error["request_id"] as? String, !requestID.isEmpty else {
            return message
        }
        return "\(message) Request ID: \(requestID)"
    }

    private static func validationMessage(from json: [String: Any]) -> String? {
        let keys = ["detail", "non_field_errors", "username", "email", "password", "password_confirm"]

        for key in keys {
            guard let value = json[key], let message = firstMessage(in: value) else { continue }
            switch key {
            case "detail", "non_field_errors":
                return message
            case "password_confirm":
                return "Password: \(message)"
            default:
                let label = key.replacingOccurrences(of: "_", with: " ").capitalized
                return "\(label): \(message)"
            }
        }
        for (key, value) in json.sorted(by: { $0.key < $1.key }) {
            guard let message = firstMessage(in: value) else { continue }
            return "\(key.replacingOccurrences(of: "_", with: " ").capitalized): \(message)"
        }
        return nil
    }

    private static func firstMessage(in value: Any) -> String? {
        if let message = value as? String, !message.isEmpty {
            return message
        }
        if let messages = value as? [Any] {
            return messages.lazy.compactMap { firstMessage(in: $0) }.first
        }
        if let nested = value as? [String: Any] {
            return validationMessage(from: nested)
        }
        return nil
    }

    private static func tunnelUnavailableMessage(host: String) -> String {
        """
        Can't reach \(host). Start the Spine backend on your Mac, then start the Cloudflare tunnel (cloudflared). On a physical iPhone, localhost won't work — the tunnel or your Mac's LAN IP must be reachable.
        """
    }

    private static func serverUnavailableMessage(host: String, statusCode: Int) -> String {
        "The server at \(host) returned HTTP \(statusCode). Check that the backend is running."
    }

    private static func networkMessage(for error: Error) -> String {
        let host = AppConfig.apiBaseURL.host ?? "the API"

        guard let urlError = error as? URLError else {
            return "Network error: \(error.localizedDescription)"
        }

        switch urlError.code {
        case .timedOut:
            return "Timed out reaching \(host)."
        case .notConnectedToInternet, .cannotConnectToHost, .networkConnectionLost:
            return tunnelUnavailableMessage(host: host)
        default:
            return urlError.localizedDescription
        }
    }
}
