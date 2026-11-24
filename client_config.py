"""
Client and Candidate Configuration Module for C2C Outreach Automation.
Stores candidate profiles, target search mappings, exclusion keyword lists,
and a comprehensive 60+ technical skills taxonomy for JD keyword matching.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
CLIENTS_DIR = BASE_DIR / "clients"
SCRATCH_DIR = BASE_DIR / "scratch"

# Ensure runtime directories exist
CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
SCRATCH_DIR.mkdir(parents=True, exist_ok=True)

# System recipient fallbacks for CC/BCC tracking
DEFAULT_ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
DEFAULT_TEAM_LEAD_EMAIL = os.getenv("TEAM_LEAD_EMAIL", "teamlead@example.com")

# ==============================================================================
# Comprehensive Technical Skills Taxonomy (60+ JD Keywords)
# ==============================================================================
JD_KEYWORDS = [
    # Programming Languages
    "Python", "Java", "Go", "Golang", "TypeScript", "JavaScript", "C#", "C++", "Scala", "Kotlin", "Rust", "Ruby",
    # Frameworks & Runtimes
    "Spring Boot", "Spring Cloud", "FastAPI", "Django", "Flask", "Node.js", "Express.js", "Next.js", "React",
    "Angular", "Vue.js", "ASP.NET", ".NET Core", "Redux", "GraphQL", "REST API",
    # Cloud & Serverless
    "AWS", "Amazon Web Services", "Azure", "GCP", "Google Cloud Platform", "AWS Lambda", "Serverless",
    "CloudFormation", "Terraform", "OpenShift",
    # Containers & Orchestration
    "Docker", "Kubernetes", "K8s", "EKS", "AKS", "GKE", "Helm", "ArgoCD",
    # Databases & Storage
    "PostgreSQL", "MySQL", "Oracle", "SQL Server", "MongoDB", "DynamoDB", "Cassandra", "Redis", "Elasticsearch",
    "OpenSearch", "Snowflake", "BigQuery", "Redshift",
    # Big Data & Streaming
    "Apache Kafka", "Kafka", "Apache Spark", "Spark", "PySpark", "Hadoop", "Airflow", "Databricks", "RabbitMQ", "ActiveMQ",
    # DevOps, CI/CD & Infrastructure
    "CI/CD", "Jenkins", "GitHub Actions", "GitLab CI", "Ansible", "Linux", "Bash", "Prometheus", "Grafana",
    "Splunk", "Datadog", "SonarQube",
    # AI/ML & LLM Frameworks
    "PyTorch", "TensorFlow", "HuggingFace", "LangChain", "LlamaIndex", "Vector Database", "Pinecone", "Milvus",
    # Testing & Methodologies
    "Microservices", "System Design", "TDD", "Agile", "Scrum", "JIRA", "Kafka Streams"
]

# ==============================================================================
# Candidate Search Roles & C2C Filtering Rules
# ==============================================================================
CANDIDATE_ROLES = {
    "safi": {
        "primary_role": "Senior Full Stack Java Engineer",
        "search_roles": [
            "Senior Java Developer",
            "Java Full Stack Developer",
            "Lead Java Engineer",
            "Backend Java Microservices Engineer"
        ],
        "exclusion_keywords": [
            "hotlist", "fulltime", "available consultants", "bench", "w2 only",
            "c2h", "1099 only", "on my bench", "consultant available", "freshers"
        ],
        "customizer_module": "safi_customizer"
    },
    "tanuja": {
        "primary_role": "Senior Python / Data / AI Engineer",
        "search_roles": [
            "Senior Python Developer",
            "Python Data Engineer",
            "AI / ML Engineer",
            "Python Backend Engineer"
        ],
        "exclusion_keywords": [
            "hotlist", "fulltime", "available consultants", "bench", "w2 only",
            "c2h", "1099 only", "on my bench", "consultant available", "intern"
        ],
        "customizer_module": "tanuja_customizer"
    },
    "alex_dev": {
        "primary_role": "Senior AWS DevOps & Cloud Architect",
        "search_roles": [
            "DevOps Engineer",
            "Cloud Platform Architect",
            "AWS Solutions Engineer",
            "Kubernetes Site Reliability Engineer"
        ],
        "exclusion_keywords": [
            "hotlist", "fulltime", "available consultants", "bench", "w2 only",
            "c2h", "1099 only", "on my bench", "consultant available"
        ],
        "customizer_module": None  # Uses default resume_customizer
    }
}

# ==============================================================================
# Candidate Profiles (CLIENTS)
# ==============================================================================
CLIENTS = {
    "safi": {
        "name": "Safi Ullah",
        "email": "safi.ullah.engineer@gmail.com",
        "phone": "+1 (469) 555-0182",
        "linkedin": "https://www.linkedin.com/in/safi-ullah-dev",
        "location": "Dallas, TX",
        "relocation": "Open to Remote or Relocation (US-wide)",
        "work_auth": "US Citizen (Corp-to-Corp / C2C Eligible)",
        "availability": "Immediate / 1 Week Notice",
        "experience": "9+ Years of hands-on Software Engineering experience",
        "resume_candidates": [
            str(CLIENTS_DIR / "safi_resume.docx"),
            str(CLIENTS_DIR / "safi_resume.pdf")
        ]
    },
    "tanuja": {
        "name": "Tanuja Patel",
        "email": "tanuja.patel.cloud@gmail.com",
        "phone": "+1 (512) 555-0144",
        "linkedin": "https://www.linkedin.com/in/tanuja-patel-data",
        "location": "Austin, TX",
        "relocation": "Remote / Nationwide",
        "work_auth": "Green Card Holder (Corp-to-Corp / C2C Eligible)",
        "availability": "2 Weeks Notice",
        "experience": "8+ Years in Python, Data Pipelines, and Distributed Systems",
        "resume_candidates": [
            str(CLIENTS_DIR / "tanuja_resume.docx"),
            str(CLIENTS_DIR / "tanuja_resume.pdf")
        ]
    },
    "alex_dev": {
        "name": "Alex Mercer",
        "email": "alex.mercer.devops@gmail.com",
        "phone": "+1 (408) 555-0199",
        "linkedin": "https://www.linkedin.com/in/alex-mercer-cloud",
        "location": "San Jose, CA",
        "relocation": "Remote (US)",
        "work_auth": "US Citizen (Corp-to-Corp / C2C Eligible)",
        "availability": "Immediate",
        "experience": "10+ Years leading Cloud Infrastructure, K8s, and CI/CD",
        "resume_candidates": [
            str(CLIENTS_DIR / "alex_resume.docx"),
            str(CLIENTS_DIR / "alex_resume.pdf")
        ]
    }
}
