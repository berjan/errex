//! Fingerprint derivation for grouping similar events.
//!
//! Stable SHA-256 grouping by explicit SDK fingerprint or exception + in-app
//! frame. Log events use their message template, not changing parameters.

use serde_json::json;
use sha2::{Digest, Sha256};

use errex_proto::{Event, Fingerprint};

pub fn derive(event: &Event) -> Fingerprint {
    let default = if let Some(ex) = event.primary_exception() {
        let frame = ex.first_frame();
        json!([
            "exception",
            ex.ty,
            frame.and_then(|f| f.function.as_ref()),
            frame.and_then(|f| f.filename.as_ref())
        ])
    } else if let Some(msg) = event.log_message(true).or(event.message.as_deref()) {
        json!(["message", msg])
    } else {
        json!(["event", event.event_id])
    };
    let key = match event
        .additional
        .get("fingerprint")
        .and_then(|v| v.as_array())
    {
        Some(parts) if !parts.is_empty() && parts.iter().all(|p| p.is_string()) => {
            json!([
                "custom",
                parts
                    .iter()
                    .map(|p| {
                        if p.as_str() == Some("{{ default }}") {
                            default.clone()
                        } else {
                            p.clone()
                        }
                    })
                    .collect::<Vec<_>>()
            ])
        }
        _ => default,
    };
    Fingerprint::new(format!("{:x}", Sha256::digest(key.to_string().as_bytes())))
}

#[cfg(test)]
mod tests {
    //! Fingerprint behavior tests. We pin the *contract* (what produces
    //! equal vs distinct fingerprints). SHA-256 over JSON arrays gives a
    //! platform-independent encoding without ambiguous string concatenation.

    use super::*;
    use chrono::Utc;
    use errex_proto::{ExceptionContainer, ExceptionInfo, Frame, Stacktrace};
    use uuid::Uuid;

    #[test]
    fn migration_custom_fingerprints_and_logging_templates() {
        let event = |raw| serde_json::from_value::<Event>(raw).unwrap();
        let a = event(serde_json::json!({"message":"same", "fingerprint":["A"]}));
        let b = event(serde_json::json!({"message":"same", "fingerprint":["B"]}));
        assert_ne!(derive(&a), derive(&b));
        let log_a =
            event(serde_json::json!({"logentry":{"message":"Failed %s", "formatted":"Failed A"}}));
        let log_b =
            event(serde_json::json!({"logentry":{"message":"Failed %s", "formatted":"Failed B"}}));
        assert_eq!(derive(&log_a), derive(&log_b));
    }

    fn ev_with_exception(ty: &str, function: &str, filename: &str, lineno: u32) -> Event {
        Event {
            additional: Default::default(),
            event_id: Uuid::new_v4(),
            timestamp: Utc::now(),
            platform: None,
            level: None,
            environment: None,
            release: None,
            server_name: None,
            message: None,
            exception: Some(ExceptionContainer {
                additional: Default::default(),
                values: vec![ExceptionInfo {
                    additional: Default::default(),
                    ty: Some(ty.into()),
                    value: None,
                    module: None,
                    stacktrace: Some(Stacktrace {
                        additional: Default::default(),
                        frames: vec![Frame {
                            additional: Default::default(),
                            filename: Some(filename.into()),
                            function: Some(function.into()),
                            module: None,
                            lineno: Some(lineno),
                            colno: None,
                            in_app: Some(true),
                        }],
                    }),
                }],
            }),
            breadcrumbs: None,
            tags: None,
            contexts: None,
            extra: None,
            user: None,
            request: None,
        }
    }

    fn ev_message(msg: &str) -> Event {
        Event {
            additional: Default::default(),
            event_id: Uuid::new_v4(),
            timestamp: Utc::now(),
            platform: None,
            level: None,
            environment: None,
            release: None,
            server_name: None,
            message: Some(msg.into()),
            exception: None,
            breadcrumbs: None,
            tags: None,
            contexts: None,
            extra: None,
            user: None,
            request: None,
        }
    }

    fn ev_empty() -> Event {
        Event {
            additional: Default::default(),
            event_id: Uuid::new_v4(),
            timestamp: Utc::now(),
            platform: None,
            level: None,
            environment: None,
            release: None,
            server_name: None,
            message: None,
            exception: None,
            breadcrumbs: None,
            tags: None,
            contexts: None,
            extra: None,
            user: None,
            request: None,
        }
    }

    // ----- shape -----

    #[test]
    fn output_is_64_hex_chars() {
        let fp = derive(&ev_with_exception("E", "f", "a.js", 1));
        let s = fp.as_str();
        assert_eq!(s.len(), 64);
        assert!(
            s.chars().all(|c| c.is_ascii_hexdigit()),
            "non-hex char: {s}"
        );
    }

    #[test]
    fn deterministic_across_calls() {
        let a = derive(&ev_with_exception("E", "f", "a.js", 1));
        let b = derive(&ev_with_exception("E", "f", "a.js", 1));
        assert_eq!(a, b, "same inputs must hash identically");
    }

    // ----- grouping (same fingerprint) -----

    #[test]
    fn lineno_and_event_id_do_not_affect_grouping() {
        // Two events with identical type+function+filename but different
        // lineno and event_id must group together. Otherwise every event
        // would be its own issue and the daemon's value vanishes.
        let a = derive(&ev_with_exception(
            "TypeError",
            "checkout",
            "src/pay.ts",
            10,
        ));
        let b = derive(&ev_with_exception(
            "TypeError",
            "checkout",
            "src/pay.ts",
            273,
        ));
        assert_eq!(a, b);
    }

    // ----- distinction (different fingerprints) -----

    #[test]
    fn different_exception_types_produce_different_fingerprints() {
        let a = derive(&ev_with_exception("TypeError", "f", "a.js", 1));
        let b = derive(&ev_with_exception("ReferenceError", "f", "a.js", 1));
        assert_ne!(a, b);
    }

    #[test]
    fn different_functions_produce_different_fingerprints() {
        let a = derive(&ev_with_exception("E", "alpha", "a.js", 1));
        let b = derive(&ev_with_exception("E", "beta", "a.js", 1));
        assert_ne!(a, b);
    }

    #[test]
    fn different_filenames_produce_different_fingerprints() {
        let a = derive(&ev_with_exception("E", "f", "a.js", 1));
        let b = derive(&ev_with_exception("E", "f", "b.js", 1));
        assert_ne!(a, b);
    }

    // ----- fallbacks -----

    #[test]
    fn message_only_events_group_by_message() {
        let a = derive(&ev_message("Database connection lost"));
        let b = derive(&ev_message("Database connection lost"));
        let c = derive(&ev_message("Different message"));
        assert_eq!(a, b);
        assert_ne!(a, c);
    }

    #[test]
    fn empty_event_falls_back_to_event_id() {
        // No exception, no message: each event must be its own group rather
        // than collapsing all "unknown" events into a single noisy bucket.
        let a = derive(&ev_empty());
        let b = derive(&ev_empty());
        assert_ne!(a, b, "two empty events must produce distinct fingerprints");
    }

    #[test]
    fn exception_takes_precedence_over_message() {
        // If an event has BOTH a message and an exception, the exception
        // is the grouping key. (The message often differs per occurrence
        // even when the exception is the same.)
        let mut with_ex = ev_with_exception("E", "f", "a.js", 1);
        with_ex.message = Some("changing message 1".into());
        let mut with_ex2 = ev_with_exception("E", "f", "a.js", 1);
        with_ex2.message = Some("changing message 2".into());
        assert_eq!(derive(&with_ex), derive(&with_ex2));
    }
}
