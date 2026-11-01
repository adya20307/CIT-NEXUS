# 🎓 CIT Nexus
### AI-Powered Campus Intelligence & Smart Scheduling System

<p align="center">
  <img src="frontend/public/images/cit-nexus-logo.png" alt="CIT Nexus Logo" width="180"/>
</p>

<p align="center">
  <strong>Think. Schedule. Adapt. One Connected Campus.</strong>
</p>

<p align="center">
  An intelligent campus management platform designed to simplify academic scheduling, automate institutional workflows, and improve communication between students, faculty, and administrators.
</p>

---

## 📌 About CIT Nexus

**CIT Nexus** is an AI-powered campus intelligence and smart scheduling platform that brings academic and administrative activities into one unified digital ecosystem.

The platform combines Artificial Intelligence, constraint-based optimization, centralized authentication, and database-driven workflows to make campus operations more efficient, transparent, and accessible.

It aims to reduce manual scheduling, simplify room allocation, streamline institutional requests, and provide intelligent assistance through natural language.

## 🎯 Problem Statement

Educational institutions often manage academic timetables, examinations, room bookings, faculty availability, and institutional requests through disconnected systems or manual processes.

This can lead to:

- Timetable clashes and inefficient room utilization.
- Difficulty managing faculty availability.
- Delays in handling scheduling changes.
- Manual examination and event scheduling.
- Limited visibility into institutional requests.
- Inefficient communication between campus stakeholders.

CIT Nexus addresses these challenges through a centralized intelligent campus management platform.

## 💡 Our Solution

CIT Nexus provides a unified platform with role-based dashboards, AI-powered assistance, and automated scheduling.

Its scheduling engine uses constraint-based optimization to generate feasible timetables while considering academic and institutional requirements.

The AI assistant helps users interact with campus services using natural language, while administrators retain control over important scheduling and approval decisions.

## ✨ Key Features

### 1. Smart Academic Timetable
- Automated timetable generation using Google OR-Tools.
- Faculty, room, and student-group conflict prevention.
- Room capacity and availability considerations.
- Multiple timetable views.
- Timetable comparison and modification support.

### 2. AI-Powered Campus Assistant
- Natural-language campus assistance using Google Gemini.
- Campus-related query handling.
- Faculty scheduling request interpretation.
- Intelligent assistance for institutional workflows.

### 3. Examination Scheduling
- Automated examination timetable generation.
- Room allocation based on examination requirements.
- Conflict-aware scheduling.
- Examination schedule management.

### 4. Room Booking Management
- Centralized room and venue booking requests.
- Booking priority management.
- Capacity and availability validation.
- Booking decision tracking.

### 5. Disruption Management
- Handle faculty unavailability.
- Handle room closures.
- Modify or reschedule affected sessions.
- Generate alternative schedules.
- Minimize unnecessary timetable changes.

### 6. Role-Based Dashboards

**Student Dashboard**
- Academic timetable.
- Examination schedule.
- Notices and campus information.
- Personal profile.

**Faculty Dashboard**
- Personal timetable.
- Faculty availability.
- Scheduling requests.
- Academic information.

**Administrator Dashboard**
- Dataset management.
- Timetable generation.
- Booking management.
- Examination scheduling.
- Disruption management.
- Institutional administration.

### 7. Authentication and Security
- Keycloak-based authentication.
- Role-based access control.
- Protected backend APIs.
- Centralized identity management.

### 8. Dataset Management
- JSON and CSV dataset support.
- Dataset validation.
- Academic data processing.
- Schedule export and validation.

> Refer to `IMPLEMENTATION_STATUS.md` for the current implementation and verification status of individual features.

## 🏗️ System Architecture

```mermaid
flowchart TD
    A[Students / Faculty / Admin] --> B[React Frontend]
    B --> C[FastAPI Backend]
    B --> D[Keycloak Authentication]
    C --> E[PostgreSQL Database]
    C --> F[Google Gemini AI]
    C --> G[Google OR-Tools]
    G --> H[Timetable Optimization]
    F --> I[Natural Language Processing]
    H --> E
    I --> C
    C --> J[Campus Services and APIs]
```

## 🛠️ Technology Stack

| Layer | Technology |
|---|---|
| Frontend | React.js |
| Build Tool | Vite |
| Backend | Python FastAPI |
| Database | PostgreSQL |
| Authentication | Keycloak |
| AI Integration | Google Gemini API |
| Optimization | Google OR-Tools |
| Data Formats | JSON, CSV |
| Containerization | Docker |

## 📂 Project Structure

```text
CIT-NEXUS/
│
├── frontend/
│   ├── public/
│   │   └── images/
│   ├── src/
│   ├── package.json
│   └── .env.example
│
├── backend/
│   ├── app/
│   ├── tests/
│   ├── requirements.txt
│   └── .env.example
│
├── keycloak/
│   ├── themes/
│   ├── realm-export/
│   └── docker-compose.yml
│
├── starter-pack/
│   └── CrunchWeek_StarterPack/
│
├── exports/
│   ├── schedule.json
│   └── schedule_after.json
│
├── IMPLEMENTATION_STATUS.md
├── README.md
└── .gitignore
```

*The structure is illustrative. Refer to the actual repository for the current file organization.*

## ⚙️ Installation and Setup

### Prerequisites

Install:

- Python 3.10 or later (use the version supported by backend dependencies).
- Node.js and npm.
- PostgreSQL.
- Docker Desktop.
- Git.

### Step 1: Clone the Repository

```bash
git clone https://github.com/adya20307/CIT-NEXUS.git

cd CIT-NEXUS
```

### Step 2: Backend Setup

```powershell
cd backend

python -m venv .venv

.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

Configure the backend environment variables using the provided `.env.example`, if available.

Set the required database connection and Gemini API configuration in the backend environment.

Start the backend:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Backend API:

http://127.0.0.1:8000

API documentation:

http://127.0.0.1:8000/docs

### Step 3: Frontend Setup

Open another terminal:

```powershell
cd frontend

npm install

npm run dev
```

Frontend:

http://localhost:5173

### Step 4: Keycloak Setup

Open another terminal:

```powershell
cd keycloak

docker compose up -d
```

Keycloak:

http://localhost:8080

Configure the `cit-nexus` realm, frontend client, backend audience, and required roles according to the project's Keycloak configuration.

### Step 5: Database Configuration

Create the PostgreSQL database and configure the backend connection string.

Apply database migrations using the migration commands configured in the project.

Do not commit database passwords, API keys, or other environment secrets.

## 🔐 Environment Variables

Create local environment files using the provided `.env.example` templates.

Typical backend configuration:

```env
DATABASE_URL=postgresql://username:password@localhost:5432/cit_nexus

GEMINI_API_KEY=your_gemini_api_key

GEMINI_MODEL=your_configured_gemini_model
```

Configure Keycloak-related settings according to the existing application configuration.

**Security guidelines:**
- Never expose Gemini API keys in frontend code.
- Never commit `.env` files.
- Keep credentials and secrets outside Git.
- Use environment variables for sensitive configuration.

## 🧠 Intelligent Scheduling Workflow

CIT Nexus uses constraint-based optimization to generate and manage academic schedules.

The general workflow:

1. Load institutional datasets.
2. Validate input data.
3. Process courses, faculty, rooms, and student groups.
4. Apply scheduling constraints.
5. Generate feasible schedules using OR-Tools.
6. Validate scheduling conflicts.
7. Review and approve proposals through the administrative workflow.
8. Export the resulting timetable.

For disruptions, the system processes affected sessions and attempts to generate a valid alternative schedule while minimizing unnecessary changes.

## 🧪 Testing

### Frontend

```bash
cd frontend

npm run lint

npm test

npm run build
```

### Backend

```bash
cd backend

pytest
```

For official scheduling verification, use the supplied starter-pack validator with the required dataset and schedule paths.

## 📍 Application URLs

| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| Backend | http://127.0.0.1:8000 |
| API Documentation | http://127.0.0.1:8000/docs |
| Keycloak | http://localhost:8080 |

## 🚀 Future Enhancements

- Complete timetable approval and version management.
- Persistent institutional audit history.
- Complete faculty scheduling request workflow.
- Comprehensive student, faculty, and administrator profile management.
- Complete PostgreSQL persistence across all workflows.
- Real-time notifications.
- Advanced institutional analytics.
- Mobile-friendly campus experience.
- Enhanced AI-powered campus automation.

## 👥 Target Users

| User | Purpose |
|---|---|
| Students | Access academic information and campus services |
| Faculty | Manage schedules and submit scheduling requests |
| Administrators | Manage academic operations and institutional resources |

## 🔒 Security and Privacy

CIT Nexus is designed around authenticated access and role-based permissions.

Access to sensitive operations should be enforced by backend authorization, not merely by hiding frontend controls.

Sensitive configuration must remain in environment variables and must not be committed to the repository.

## 🤝 Contributing

Contributions, suggestions, and improvements are welcome.

1. Fork the repository.
2. Create a feature branch.
3. Make your changes.
4. Run the relevant tests.
5. Submit a pull request.

## 📄 License

No license has been specified yet. A license should be added before permitting external reuse or redistribution.

## 🌟 Project Information

**Project:** CIT Nexus

**Category:** AI-Powered Campus Intelligence & Smart Scheduling

**GitHub:** https://github.com/adya20307/CIT-NEXUS

<p align="center">
  <strong>Think. Schedule. Adapt.</strong><br/>
  One Connected Campus. A Smarter Academic Experience.
</p>
