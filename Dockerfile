FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PORT=7860

# Install system dependencies (FFmpeg, LaTeX, Cairo, etc. for Manim)
# We update apt and install the heavy lifting packages required by Manim to render animations
RUN apt-get update && apt-get install -y \
    ffmpeg \
    texlive \
    texlive-latex-extra \
    texlive-fonts-extra \
    texlive-latex-recommended \
    texlive-science \
    tipa \
    libcairo2-dev \
    libpango1.0-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces require the app to run as user 1000
RUN useradd -m -u 1000 user

# Switch to the "user"
USER user

# Set home to the user's home directory
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

# Set the working directory
WORKDIR $HOME/app

# Copy the requirements file and install Python dependencies
COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY --chown=user:user . .

# Create the necessary directories with correct permissions for the app to save files locally
RUN mkdir -p media final_videos static_videos images

# Expose the port Hugging Face expects
EXPOSE 7860

# Run the FastAPI application on port 7860
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]
