const fileInput = document.getElementById('fileInput');
const fileListPreview = document.getElementById('fileListPreview');
const generateBtn = document.getElementById('generateBtn');
const outputText = document.getElementById('outputText');
const copyBtn = document.getElementById('copyBtn');
const loading = document.getElementById('loading');

// Display selected files and enforce the 8-file limit
fileInput.addEventListener('change', function() {
    const files = this.files;
    fileListPreview.style.display = 'block';
    
    if (files.length === 0) {
        fileListPreview.style.display = 'none';
        return;
    }

    if (files.length > 8) {
        alert("Please select a maximum of 8 files.");
        this.value = ""; // Clear the selection
        fileListPreview.style.display = 'none';
        return;
    }

    // Create a list of file names to show the user
    let fileNames = "<strong>Selected Files:</strong><br>";
    for (let i = 0; i < files.length; i++) {
        fileNames += `- ${files[i].name}<br>`;
    }
    fileListPreview.innerHTML = fileNames;
});

// Handle the generation process
generateBtn.addEventListener('click', async () => {
    const files = fileInput.files;
    
    if (files.length === 0) {
        alert("Please select at least one file first.");
        return;
    }

    // Package ALL files to send to Python
    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
        formData.append('files', files[i]);
    }

    // Update UI to show loading state
    generateBtn.disabled = true;
    loading.style.display = 'block';
    outputText.value = '';

    try {
        const response = await fetch('/generate-vocab', {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        if (response.ok) {
            outputText.value = data.text; // Output the result
        } else {
            alert("API Error: " + data.error);
        }
    } catch (error) {
        alert("A network error occurred.");
        console.error(error);
    } finally {
        generateBtn.disabled = false;
        loading.style.display = 'none';
    }
});

// One-Click Copy functionality
copyBtn.addEventListener('click', () => {
    if (!outputText.value) {
        alert("No text to copy yet!");
        return;
    }
    
    navigator.clipboard.writeText(outputText.value).then(() => {
        const originalText = copyBtn.innerText;
        copyBtn.innerText = "Copied!";
        setTimeout(() => {
            copyBtn.innerText = originalText;
        }, 2000);
    }).catch(err => {
        alert("Failed to copy text. Please copy manually.");
    });
});