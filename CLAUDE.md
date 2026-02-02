App Name: Health Advisor 

I'm building a healthcare demo application to help my team understand agentic AI, MCP (Model Context Protocol), and AI security risks. This is a learning/testing environment where we'll eventually demonstrate vulnerabilities like insecure communications, prompt injections, MCP misconfigurations, and other AI-specific security issues.

The application needs:
- A simple web frontend with a chatbot interface
- An AI agent that can answer questions like "what prescriptions do I have?" or "when is my next appointment?"
- An MCP client/server architecture
- A database with fake patient health information (PHI)
- All of this running locally on my machine initially

The end goal is to:
1. Understand how agentic AI systems work
2. Identify and demonstrate security vulnerabilities
3. Document setup steps so coworkers can replicate this
4. Eventually map security findings back to control standards

Don't make assumptions - interview me first so we build this correctly for my learning goals.

Here are some of the requirements. 

I want to use FastAPI for the backend (all Python), and vanilla JS/HTML for the frontend (at least initially), and then a SQLite db. 

Build from scratch using the MCP SDK. I want to understand how MCP works from the ground up.

-

Use OpenAI API (GPT-4) for now - it's the most straightforward to get started with and most team members likely already have access. We can always add other models later if needed.

For MCP tools/resources to expose, let's start with:

Fake PHI Data
* Patient demographics (name, DOB, address)
* Medical records (diagnoses, conditions)
* Prescriptions (medications, dosages)
* Appointments (dates, providers)
* Insurance information
* Lab results

For the initial build, implement standard security practices:
- Basic authentication
- Proper input validation
- Secure MCP configuration

Let's hold off on implementing vulnerabilities for now. First, I want to build a functional baseline version that works correctly and securely. Once I understand how the system operates normally and can see the traffic flow, we'll iterate and intentionally introduce vulnerabilities as a separate phase.

For now, just build it with reasonable security practices as a starting point.

We'll document the "correct" way first, then create vulnerable versions later for comparison and testing.

- Project purpose: Learning MCP, agentic AI, and security testing. Our team wants to create 
- Tech stack: Python/FastAPI, SQLite, vanilla JS, MCP SDK
- Key principles: Search for actual docs before coding, prioritize observability, config-driven security
- Previous learnings: Tried to use Windsurf initially, and it was hallunicating when trying to create APIs. 
- Architecture goals: Simple, working baseline first, then add complexity

App Title: "Health Advisor" (display prominently in the UI)

Patient Context Display:
Show the logged-in patient's basic info at the top:
- Patient name
- Date of birth
- Member ID number

Main Interface:
- Display the chatbot conversation area
- Show example prompts/questions the user can ask, like:
  * "What prescriptions do I currently have?"
  * "When is my next appointment?"
  * "Show me my recent lab results"
  * "What is my primary care physician's name?"

Keep the design clean and simple - this is a demo, not a production app.

Keep everything as simple local servers for now - no Docker or containerization at this stage.

The setup needs to be easily replicated by coworkers, so let's achieve that through:
- Clear step-by-step setup documentation (we'll create a SETUP.md file)
- Simple Python virtual environment (venv) for dependencies
- Local SQLite database (just a file)
- All components running as local processes on different ports

Generate a requirements.txt and clear instructions for:
1. Installing dependencies
2. Setting up the database
3. Starting each component (MCP server, backend, frontend)
4. What ports everything runs on

We'll handle containerization and cloud deployment in a future phase once we understand the architecture. For now, prioritize simplicity and ease of replication on any developer's machine.

MCP OBSERVABILITY & DEBUGGING:
- Build in request/response logging for all MCP communications
- Show the actual MCP protocol messages (transport layer visibility)
- Display whether we're using SSE (Server-Sent Events), HTTP streaming, or standard HTTP
- Include a debug panel or logging view in the UI that shows:
  * Tool calls being made
  * MCP requests/responses in real-time
  * Agent reasoning/decision flow
  * Database queries being executed

CONFIGURATION-DRIVEN SECURITY CONTROLS:
Create a config file (config.yaml or similar) where we can toggle security features on/off:
- Authentication enabled/disabled
- Input validation strictness (none/basic/strict)
- MCP server authentication required (yes/no)
- Prompt injection protection (enabled/disabled)
- Data filtering in responses (enabled/disabled)
- Logging verbosity levels
- Rate limiting on/off

This lets us demonstrate "here's what happens when we disable X control."

AI-SPECIFIC VULNERABILITY DEMONSTRATION:
Build the system so we can easily demonstrate:
- Prompt injection attacks (agent following malicious instructions in user input)
- Indirect prompt injection (malicious instructions in database/retrieved data)
- Data exfiltration through clever prompting
- Tool misuse (calling unintended MCP tools)
- Authorization bypass (accessing other patients' data)
- Insecure MCP communication patterns

MAPPING TO CONTROL STANDARDS:
Structure the code and config so each security control maps to:
- Where it's implemented (code location)
- What vulnerability it prevents
- How to test if it's working
- What happens when disabled

The goal is to create a living reference implementation where we can point to specific controls, explain why they matter, demonstrate attacks when absent, and show proper implementation when present.

Can you design the architecture with these observability and configurability requirements in mind? Prioritize visibility into what's happening at each layer.

## Instructions for Claude
- Always search for actual MCP SDK documentation before implementing
- Test each component individually before integration  
- Keep dependencies in requirements.txt up to date
- Prioritize clear, working code over clever abstractions
