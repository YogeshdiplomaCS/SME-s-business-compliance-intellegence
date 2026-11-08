# SME Business Compliance Intelligence

A Streamlit project for exploring business compliance workflows. The repository includes Python application modules, document-processing code, and SQL assets. Check the source and demo data carefully before using it with real business documents.

## Run locally

1. Install Python 3.10 or later.
2. Clone the repository and enter its folder:

   ```bash
   git clone https://github.com/YogeshdiplomaCS/SME-s-business-compliance-intellegence.git
   cd SME-s-business-compliance-intellegence
   ```

3. Create and activate a virtual environment, then install dependencies:

   ```bash
   python -m venv .venv
   # Linux/macOS: source .venv/bin/activate
   # Windows PowerShell: .venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

4. Configure any database or service settings required by the application using local environment variables. Do not commit credentials. Review the application configuration and SQL files to determine the required names and setup.
5. Start the Streamlit app:

   ```bash
   streamlit run app.py
   ```

## Notes

- Dependencies are listed once in `requirements.txt`; choose a disposable development database when testing.
- Document requirements and test with sample documents before processing real or sensitive records.
- Add screenshots, database setup/migration steps, and verified feature details as the project is tested.
