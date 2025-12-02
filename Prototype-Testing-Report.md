# Prototype Testing Report – CiphERA Decentralized Identity Prototype

# 1. Executive Summary
- Purpose of prototype: Validate decentralized identity lifecycle management for multi-node access control.
- Scope of testing: Gateway API, face recognition pipeline, distributed user stores, browser client flows.
- High-level outcome: Core identity verification flow succeeds; secondary node synchronization partially reliable.
- Final verdict (Pass / Conditional Pass / Fail): Conditional Pass

# 2. Prototype Overview
- System description: Hybrid edge/cloud platform issuing and verifying decentralized credentials for biometric-authenticated login.
- Core objectives: Ensure secure enrollment, authentication, and revocation across distributed nodes.
- Expected users: Security admins, enrolled employees, auditing staff.
- Key features: Biometric capture, verifiable credential issuance, multi-node validation, event auditing dashboard.
- Technology stack: Python FastAPI, Flask microservices, TensorFlow Lite face model, IPFS-backed credential store, PostgreSQL, Redis Pub/Sub, WebAuthn-compatible client.

# 3. Test Scope
- In-scope components: `gateway/main_api.py`, node authenticator services, face recognition model pipeline, client SPA, credential database.
- Out-of-scope components: Hardware camera firmware, enterprise IAM integration adapters, production monitoring stack.
- Assumptions: Stable biometric capture hardware, trusted enrollment operator, synchronized system clocks (±2s).
- Constraints: Prototype nodes hosted on shared VLAN, limited GPU resources, manual failover operations.

# 4. Test Environment
| Component | Specification | Version | Notes |
| Hardware | Dell Latitude 7440, Intel i7-1365U, 32 GB RAM | BIOS 1.9.3 | Local test rig |
| OS | Windows 11 Pro 23H2 | Build 22631.2861 | All nodes virtualized |
| Network conditions | 1 Gbps LAN, 35 ms simulated inter-node latency | n/a | NetEm applied |
| Tools and frameworks | Python 3.11.6, FastAPI, Flask, pytest, Locust | Latest pip packages | Virtual env per service |
| Databases | PostgreSQL 15.5, Redis 7.2.1 | Docker images | Clustered on host |
| APIs or services | IPFS HTTP API 0.18, WebAuthn test harness | REST/WebSocket | Hosted locally |

# 5. Test Strategy and Methodology
- Manual testing approach: Exploratory persona-based validation of enrollment, authentication, revocation, and audit trails.
- Automated testing approach (if any): Pytest functional suites, Locust load scripts for credential issuance, contract tests via Schemathesis.
- Test design techniques used (Boundary, Equivalence, Negative testing, etc.): Boundary value analysis on credential TTL, equivalence partitioning for biometric confidence, negative testing for tampered tokens and stale sessions.
- Entry criteria: Build deployed to test environment, databases seeded, test accounts provisioned, smoke tests green.
- Exit criteria: Critical defects closed or mitigated, ≥85% functional pass rate, security regressions resolved, documentation updated.

# 6. Test Cases
| Test Case ID | Description | Precondition | Steps | Expected Result | Actual Result | Status |
| TC-001 | Enroll new user via gateway | Admin token valid, camera available | Initiate enrollment; capture face; submit profile | Credential issued, status ACTIVE | As expected | Pass |
| TC-002 | Authenticate enrolled user on node1 | User enrolled, node1 online | Access login page; submit face scan | Session token issued, redirect to dashboard | As expected | Pass |
| TC-003 | Biometric mismatch rejection | User not enrolled | Attempt login with unknown face | Authentication denied, audit entry recorded | As expected | Pass |
| TC-004 | Credential revocation propagation | User enrolled, admin console reachable | Revoke credential via gateway; login on node2 | Login denied within 60s, node cache cleared | Node2 allowed login after 95s | Fail |
| TC-005 | Expired credential handling | Credential TTL set to 24h | Attempt login after TTL expiry | Renewal required, access denied | As expected | Pass |
| TC-006 | Multi-node sync under latency | Nodes linked, latency injector active | Issue credential; login on node2 after 10s | Node2 accepts credential | Timeout at 12s but retries succeed | Pass |
| TC-007 | API rate limiting | Gateway configured with 50 req/min | Execute 60 auth requests in 60s | Requests above limit return 429 | 429 returned at request 53 | Pass |
| TC-008 | Tampered JWT detection | Capture valid token | Modify payload; resend to gateway | Token rejected, incident logged | As expected | Pass |
| TC-009 | Session timeout enforcement | Session TTL 15 min | Idle session 20 min; resume activity | Force re-authentication | Session remained active | Fail |
| TC-010 | Audit log integrity | Audit DB accessible | Perform enrollment, auth, revoke | Events recorded with immutable hash | Hash mismatch for revoke | Fail |
| TC-011 | Load test credential issuance | 100 concurrent enrollments | Run Locust scenario 100 users | Avg latency <2s, error rate <2% | Latency 1.8s, error 1.2% | Pass |
| TC-012 | Client-side input validation | SPA loaded | Submit empty enrollment form | Client blocks submission, shows inline errors | As expected | Pass |

# 7. Functional Testing Results
- Feature-wise result breakdown: Enrollment 100% pass; Authentication 83% pass; Revocation 60% pass; Audit logging 75% pass; Session management 67% pass.
- Pass/fail percentage: 75% pass (9/12), 25% fail (3/12).
- Observed behavior vs expected behavior: Revocation propagation delayed on node2; session timeout handler not enforcing idle expiration; audit hash chain breaks on revoke events.

# 8. Non-Functional Testing
- Performance: Peak CPU 68% on gateway during load; p95 auth latency 820 ms under 100 concurrent users; below SLO.
- Security: JWT tampering blocked; revocation lag opens short-lived replay window; TLS mutual auth not yet enforced.
- Usability: Enrollment wizard intuitive; need clearer error copy for timeout cases; mobile viewport exhibits scroll jitter.
- Compatibility: Chrome, Edge, Firefox work; Safari fails WebAuthn fallback; Android Chrome stable; iOS requires polyfill.
- Stability: Gateway uptime 99.2% during 24h soak; node2 service restarted twice due to Redis reconnect loop.

# 9. Defects and Issues
| Defect ID | Description | Severity | Impact | Status | Resolution |
| DEF-001 | Revocation cache sync exceeds 60s on node2 | High | Users retain access post-revocation | Open | Tune Redis pub/sub, add forced pull |
| DEF-002 | Session timeout not enforced after idle | High | Dormant sessions stay valid beyond policy | In Progress | Patch middleware inactivity check |
| DEF-003 | Audit hash mismatch on revoke events | Medium | Compromises forensic integrity | Open | Recalculate hash chain post-transaction |
| DEF-004 | Safari WebAuthn fallback failure | Medium | iOS users blocked | Open | Implement WebAuthn polyfill and testing |
| DEF-005 | Redis reconnect loop under packet loss | Low | Node2 availability drops | Resolved | Increased backoff, added health probe |

# 10. Risk Assessment
- Technical risk: High impact; revocation propagation delay; mitigation—implement push/pull hybrid sync and add heartbeat monitors.
- Security risk: Medium impact; idle session persistence; mitigation—enforce absolute idle timeout and token rotation.
- Scalability risk: Medium impact; Redis bottleneck; mitigation—introduce message queue with persistence and horizontal scaling.
- Usability risk: Low impact; Safari incompatibility; mitigation—deploy cross-browser compatibility shim and UX messaging.
- Legal/compliance risk: Medium impact; audit hash integrity gap; mitigation—fix hash chain bug and add daily compliance report.

# 11. Limitations of Testing
- What was not tested: Full integration with enterprise IAM, disaster recovery failover, hardware biometric spoofing detection.
- Why it was not tested: Dependencies not available, environment timeboxed, specialized equipment pending.
- Impact of limitation: Residual risk on compliance certification, operational resilience, anti-spoof assurance.

# 12. Recommendations
- Improvements: Harden revocation dissemination, implement centralized telemetry dashboard.
- Refactors: Modularize session middleware, decouple audit hashing into dedicated service.
- Optimization: Introduce Redis cluster sharding, enable model quantization for faster inference.
- Security enhancements: Enforce mutual TLS, add device fingerprinting, implement anomaly detection alerts.
- UX improvements: Enhance timeout messaging, add progressive disclosure for enrollment errors, resolve Safari fallback.

# 13. Conclusion
- Overall system readiness: Prototype functional but requires remediation for security-critical defects prior to pilot deployment.
- Deployment recommendation: Defer production rollout until high-severity issues resolved and regression tests expanded.

# 14. Appendices
- Sample logs: See `logs/gateway_auth_2025-11-27.log` excerpt capturing revocation lag timestamps.
- Screenshots references (as placeholders): `[Screenshot_A1: Enrollment_Success.png]`, `[Screenshot_B2: Node2_Revocation_Delay.png]`.
- Configuration notes: `.env` contains IPFS endpoint overrides; `Scripts/activate.bat` sets Redis channel config for sync tests.
