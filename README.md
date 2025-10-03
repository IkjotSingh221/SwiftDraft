# SwiftDraft

This is an agentic AI project built with the Google Agent Development Kit (ADK) designed to accelerate the process of drafting research papers for STEM students. It utilizes a team of specialized AI agents that collaborate to research topics, structure content, and generate useful drafts.

## Features

  * **Agentic Collaboration:** Multiple AI agents (e.g., Researcher, Outliner, Writer) work together to produce comprehensive drafts.
  * **STEM Focused:** Tailored to understand and generate content for scientific and technical domains.
  * **Simple Web Interface:** Easily interact with the agents through a straightforward web UI powered by the ADK.

-----

## Getting Started

Follow these simple steps to get the project up and running on your local machine.

### Prerequisites

Make sure you have **Python 3.8+** and **pip** installed on your system.

### Installation & Setup

1.  **Clone the Repository**

    ```sh
    git clone https://github.com/IkjotSingh221/SwiftDraft.git
    cd SwiftDraft
    ```

2.  **Set Up Environment Variables**
    Create a `.env` file in the root of the project. You can copy the example file to get started:

    ```sh
    cp .env.example .env
    ```

    Now, open the `.env` file and add your API keys and any other required configuration values.

    ```env
    # .env
    GOOGLE_API_KEY="YOUR_GOOGLE_API_KEY_HERE"
    ```

3.  **Install Dependencies**
    Install all the required Python packages from the `requirements.txt` file.

    ```sh
    pip install -r requirements.txt
    ```

-----

## How to Run

With the setup complete, you can start the application with a single command:

```sh
adk web
```

This will launch the web server. Open your browser and navigate to the local address provided in your terminal (usually `http://127.0.0.1:8000`) to start using the research accelerator\!