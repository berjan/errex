//! An in-flight ingest must finish its transaction during SIGTERM shutdown.
#![cfg(unix)]

use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[tokio::test]
async fn migration_sigterm_drains_inflight_ingestion() {
    let dir = std::env::temp_dir().join(format!("errex-shutdown-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir_all(&dir).unwrap();
    let port = std::net::TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port();
    let mut child = tokio::process::Command::new(env!("CARGO_BIN_EXE_errexd"))
        .env("ERREX_DATA_DIR", &dir)
        .env("ERREX_HOST", "127.0.0.1")
        .env("ERREX_PORT", port.to_string())
        .env("ERREX_MCP_PORT", "0")
        .env("ERREX_REQUIRE_AUTH", "false")
        .env("ERREX_LOG_LEVEL", "error")
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let addr = format!("127.0.0.1:{port}");
    let mut stream = loop {
        if let Ok(stream) = tokio::net::TcpStream::connect(&addr).await {
            break stream;
        }
        assert!(
            child.try_wait().unwrap().is_none(),
            "daemon exited during startup"
        );
        tokio::time::sleep(Duration::from_millis(20)).await;
    };
    let db = sqlx::SqlitePool::connect(&format!("sqlite://{}", dir.join("errex.db").display()))
        .await
        .unwrap();
    let mut connection = db.acquire().await.unwrap();
    sqlx::query("BEGIN IMMEDIATE")
        .execute(&mut *connection)
        .await
        .unwrap();
    let body = "{}\n{\"type\":\"event\"}\n{\"message\":\"shutdown check\"}\n";
    let req = format!("POST /api/123/envelope/ HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body);
    stream.write_all(req.as_bytes()).await.unwrap();
    tokio::time::sleep(Duration::from_millis(200)).await;
    assert!(tokio::process::Command::new("bash")
        .args([
            "-c",
            "kill -TERM \"$1\"",
            "--",
            &child.id().unwrap().to_string()
        ])
        .status()
        .await
        .unwrap()
        .success());
    tokio::time::sleep(Duration::from_millis(100)).await;
    sqlx::query("ROLLBACK")
        .execute(&mut *connection)
        .await
        .unwrap();
    let mut response = String::new();
    tokio::time::timeout(Duration::from_secs(5), stream.read_to_string(&mut response))
        .await
        .unwrap()
        .unwrap();
    assert!(
        response.starts_with("HTTP/1.1 200"),
        "pending ingest was lost: {response}"
    );
    assert!(tokio::time::timeout(Duration::from_secs(5), child.wait())
        .await
        .unwrap()
        .unwrap()
        .success());
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM events")
        .fetch_one(&db)
        .await
        .unwrap();
    assert_eq!(count, 1);
}
