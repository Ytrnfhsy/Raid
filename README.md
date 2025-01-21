# Raid
# Image Search and Management System

## Overview
This project is a web-based application for managing and searching images using deep learning embeddings and metadata. It allows users to upload images, search for similar ones, and manage directories for bulk scanning. The application uses Flask, SQLAlchemy, Milvus for vector search, and CLIP for image processing.

## Features
- Upload and search images using deep learning embeddings.
- Automatic scanning and indexing of image directories.
- Support for multiple image formats (JPG, PNG, BMP, TIFF, PSD).
- Background scanning and scheduling.
- Detailed logging and error handling.
- Web-based interface for easy interaction.

## Technologies Used
- **Backend:** Flask, SQLAlchemy
- **Frontend:** Jinja2 (for HTML rendering)
- **Database:** SQLite
- **Vector Search:** Milvus
- **Machine Learning:** PyTorch, CLIP (by OpenAI)
- **Image Processing:** Pillow (PIL), psd-tools
- **Task Scheduling:** APScheduler

## Installation

### Prerequisites
- Python 3.8+
- Milvus (running on `localhost:19530`)
- SQLite (built-in with Python)

### Steps
1. Clone the repository:
   ```bash
   git clone https://github.com/Ytrnfhsy/Raid.git
   cd Raid
   ```

2. Run `Install.bat` (Windows) or `Install.sh` (Linux)

3. Install Milvus using docker-compose.yml
    ```
    docker-compose up -d 
    
    ```

4. Run `Start.bat` (Windows) or `Start.sh` (Linux)

## Configuration
The application uses configuration files for customizable parameters.

- **config.json:** Stores similarity threshold, max results, recursive scan options.
- **directories.json:** Stores the list of directories to scan.
- **logs.txt:** Stores event logs.
- **scan_state.json:** Tracks scan progress.

## Usage

### 1. Upload an Image
Navigate to the home page and upload an image to find similar images from indexed directories.

### 2. Directory Management
Add or remove directories for bulk scanning.

### 3. Search by Text
Enter keywords to find relevant images using embeddings.

### 4. View Logs
Check the system logs for any errors or important updates.

## API Endpoints

| Endpoint              | Method | Description                        |
|----------------------|--------|------------------------------------|
| `/`                   | GET    | Main page with search options     |
| `/add_directory`      | POST   | Add a directory for scanning      |
| `/delete_directory`   | POST   | Remove a directory                |
| `/search_image`       | POST   | Search for images by upload       |
| `/search_text`        | POST   | Search for images by text query   |
| `/get_logs`           | GET    | Retrieve system logs              |
| `/clear_logs`         | POST   | Clear system logs                 |

## Error Handling
The system includes extensive logging to track errors and scan progress. In case of issues:
- Check `logs.txt`.
- Ensure Milvus is running.
- Verify directory paths in `directories.json`.

## Future Improvements
- Adding a user authentication system.
- Integration with cloud storage solutions.
- Enhanced UI with React or Vue.js.

## License
This project is licensed under the MIT License.

## Contact
For inquiries or contributions, reach out to `hudozhka88@ukr.net`.

