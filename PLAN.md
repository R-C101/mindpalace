# Home Memory – System Design & Implementation Plan (v0.1 MVP)

## 1. What is this app?

Home Memory is a **voice-first, vision-assisted local system** that helps users remember where physical objects are stored inside their home.

Users can say things like:
- “Remember my boots are in the left drawer of the bed in the master bedroom.”
- “Where are my boots?”
- “Move my boots to the right drawer.”

The system stores a **hierarchical, nestable map of containers and items**, similar to a filesystem, and allows safe querying and updates without ambiguity or AI hallucinations.

Typing is discouraged. Voice + vision are the primary interfaces. An app UI exists mainly for secure access and visual inspection.

---

## 2. Core Design Principles

- Voice-first, zero typing for core flows
- Deterministic backend (AI never touches DB logic)
- Filesystem-like nesting with unique paths
- Security-first (locked containers never leak via voice)
- MVP favors correctness over conversational polish
- Local-first, but scalable to app + cloud later

---

## 3. Core Concepts

### 3.1 Entity Types

There are **only two fundamental runtime entities**:

1. **Container**
   - Can contain other containers or items
   - Examples: home, room, bed, drawer, suitcase, pouch

2. **Item**
   - Leaf node (cannot contain anything)
   - Examples: boots, gold, passport

Everything else (room, drawer, suitcase) is a *typed container* via metadata.

---

### 3.2 Nesting Model

Containers can be nested infinitely:

/home  
└── master bedroom  
    └── bed  
        └── left drawer  
            └── black suitcase  
                └── brown purse  
                    └── gold  

This behaves exactly like a filesystem.

---

## 4. Naming & Paths

- Names must be **unique among siblings**
- Duplicate names allowed in different branches
- Full path is always unique
- Paths are materialized and stored in DB

Example valid paths:
- /home/master-bedroom/left-drawer
- /home/guest-bedroom/left-drawer

---

## 5. Security Model

- Any container or item can be locked
- Locks propagate downward (inheritance)
- Security levels:
  - 0 = open (voice can reveal location)
  - 1 = restricted (voice confirms existence only)
  - 2 = secret (voice cannot confirm existence)

Locked content requires app authentication.

---

## 6. AI Responsibilities (Hard Boundary)

LLM **ONLY**:
- Parses voice → structured intent
- Extracts names and paths
- Generates spoken responses

LLM **NEVER**:
- Searches the database
- Traverses containers
- Resolves ambiguity
- Bypasses security
- Writes directly to storage

---

## 7. Database Design (Critical)

We use **SQL (SQLite locally, Postgres later)** with a **materialized path** approach.

### Containers table
- Stores the hierarchical tree
- Uses parent_id + path + depth
- Enforces uniqueness among siblings

### Items table
- Stores leaf nodes
- Each item belongs to exactly one container
- Items also store full path

### Images
- Both containers and items can have multiple images
- Images are stored separately and linked via a join table

---

## 8. MVP Scope (Frozen)

Included:
- Containers & items
- Infinite nesting
- Add / move / rename / delete
- Locking & security flags
- Images for containers and items
- Local DB

Excluded for now:
- Voice memory across turns
- App UI polish
- Cloud sync
- Multi-user support

---

## 9. Implementation Order (Strict)

1. Database models (containers, items, images)
2. Path resolution logic
3. Core operations (add, move, rename, lock)
4. Local API layer
5. Debug UI (Streamlit)
6. Voice & vision

No UI or AI before DB + core logic are correct.

