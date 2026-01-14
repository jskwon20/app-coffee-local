# 🛠️ 로컬 Kubernetes 기반 DevOps 플랫폼 구축 가이드

> **"로컬 환경에서도 운영 환경(EKS)과 똑같이 개발하고 배포할 수 없을까?"**
>
> 이 가이드는 Docker 기반의 Kubernetes(Kind) 환경 위에서 **CI/CD 파이프라인**, **GitOps**, 그리고 **MLTP Observability(모니터링)**까지 완벽한 DevOps 사이클을 구축한 과정을 정리한 문서입니다.

---

## 🏗️ 1. 아키텍처 (Architecture)

로컬 개발 환경(MacBook)의 특성과 Docker 네트워크 격리 문제를 극복하기 위해 다음과 같이 설계를 최적화했습니다.

### 1.1 전체 시스템 구성

```mermaid
graph TD
    User((User)) -- "https://coffee..." --> Host[MacBook (HostNetwork)]
    Host -- "Port 80/443" --> Ingress[Nginx Ingress Controller]
    
    subgraph "Kind Cluster (Docker)"
        Ingress -- "Routing" --> Frontend[Frontend (NodePort)]
        Frontend -- "ClusterIP" --> Backend[Backend Services]
        Backend --> DB[(MySQL)]
        
        ArgoCD[ArgoCD] -- "Sync" --> K8s[K8s Resources]
    end
    
    GitHub[GitHub Actions] -- "Push Image" --> ECR[AWS ECR]
    ArgoCD -- "Pull Manifests" --> GitHubRepo[GitHub Repo]
```

### 1.2 네트워크 전략 (Networking)
*   **Kind (Kubernetes in Docker):** 무거운 VM 대신 가벼운 컨테이너 기반 K8s 사용.
*   **HostNetwork 트릭:** Kind는 기본적으로 외부 접속이 어렵습니다. Nginx Ingress를 `hostNetwork: true`로 설정하여 **호스트(Mac)의 80/443 포트를 직접 점유**하도록 구성했습니다.
*   **Nip.io DNS:** 복잡한 `/etc/hosts` 설정 없이 `*.192.168.38.140.nip.io` 도메인을 사용해 즉시 서브도메인 라우팅을 구현했습니다.

---

## 🚀 2. 자동화 파이프라인 (CI/CD)

단순히 "코드 푸시 -> 자동 배포"를 넘어, 안정성을 고려한 **GitOps** 방식을 채택했습니다.

### 2.1 CI: GitHub Actions
*   **역할:** 빌드 & 이미지 관리
*   **주요 기능:**
    1.  **병렬 빌드 (Matrix Strategy):** Frontend/Backend(Order, Inventory, Billing) 이미지를 동시에 빌드하여 시간 단축.
    2.  **ECR 업로드:** AWS ECR에 보안 로그인 후 이미지 푸시.
    3.  **매니페스트 자동 업데이트:** `sed` 명령어로 K8s YAML 파일의 이미지 태그를 최신 커밋 해시로 변경 후 Git에 다시 커밋.

### 2.2 CD: ArgoCD (GitOps)
*   **역할:** 배포 & 상태 유지
*   **주요 기능:**
    *   GitHub 저장소의 `k8s/overlays/local` 폴더를 3분마다 감시.
    *   새로운 이미지 태그가 발견되면 즉시 클러스터에 반영 (Sync).
    *   **Self-Healing:** 누군가 실수로 파드를 지우거나 설정을 바꿔도, ArgoCD가 "Git과 다르잖아?"하고 즉시 원상 복구시킴.

---

## 📊 3. 모니터링 & 관제 (Observability)

서비스가 잘 돌아가는지 감시하기 위해 **MLTP 스택**을 구축했습니다.

### 3.1 스택 구성 (Helm 설치)
| 구성 요소 | 역할 | 도구 |
| :--- | :--- | :--- |
| **M**etrics | 수치 데이터 수집 (CPU, 메모리, 요청량) | **Prometheus** |
| **L**ogs | 로그 수집 (애플리케이션 로그) | **Loki + Promtail** |
| **T**races | 분산 트레이싱 (요청 추적) | **Tempo** |
| **P**latform | 통합 시각화 대시보드 | **Grafana** |

### 3.2 알람 시스템 (Alerting)
Prometheus Alertmanager를 **Slack**과 연동하여 장애 발생 시 즉시 알림을 받도록 했습니다.

*   📢 **PodRestart:** 파드가 비정상 종료 후 재시작되면 경고 (Warning).
*   🚨 **PodNotReady:** 배포가 실패하거나 파드가 준비되지 않으면 즉시 알림 (Critical).
*   🔥 **HttpHighErrorRate:** HTTP 500 에러가 급증하면 긴급 알림 (Critical).

---

## 📝 4. 트러블슈팅 일지 (Dev Diary)

구축 과정에서 겪은 주요 이슈와 해결 방법을 기록합니다.

### 🔥 Issue 1: ArgoCD 접속 불가 (Connection Refused)
*   **상황:** ArgoCD를 NodePort로 열었으나, 외부(Mac)에서 접속이 안 됨.
*   **원인:** Kind 노드는 Docker 컨테이너 내부에 있어서 localhost 포트 포워딩 없이는 접근 불가.
*   **해결:** Nginx Ingress Controller를 `hostNetwork: true`로 패치하여 **인그레스가 문지기 역할**을 하도록 변경.

### 🔥 Issue 2: 로그인 무한 실패 (404 Not Found)
*   **상황:** 프론트엔드에서 `/api/order` 호출 시 계속 404 에러.
*   **원인:** Ingress의 `rewrite-target: /` 옵션이 경로를 잘라먹고 있었음 (`/api/order` -> `/`로 전달).
*   **해결:** 백엔드가 이미 경로(`ROOT_PATH`)를 처리할 수 있으므로, Ingress의 **Rewrite 옵션 제거**.

### 🔥 Issue 3: Grafana 실행 불가 (CrashLoopBackOff)
*   **상황:** Observability 설치 후 Grafana가 계속 죽음.
*   **원인:** `kube-prometheus-stack`과 `loki-stack`이 서로 자신이 **"기본(Default) 데이터소스"**라고 우김. (Grafana는 기본 데이터소스가 2개면 실행 거부)
*   **해결:** Loki의 ConfigMap을 수정하여 `isDefault: false`로 변경.

---

## ✅ 마치며

이제 이 로컬 환경은 **AWS EKS 환경의 축소판**입니다.
이곳에서 개발, 테스트, 모니터링까지 모두 마친 코드는 **운영 환경에도 100% 안전하게 배포**될 수 있습니다.

*   **Project Path:** `~/Desktop/Workspace/python/project/coffee-local`
*   **Main Resources:** `k8s/`, `monitoring/`, `.github/`, `argocd-app.yaml`

**Happy Coding & Ops!** ☕️🚀
