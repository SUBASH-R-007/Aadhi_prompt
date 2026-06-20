
        // --- History Modal Logic ---
        const historyBtn = document.getElementById('open-history-btn');
        const historyModalOverlay = document.getElementById('history-modal-overlay');
        const closeHistoryBtn = document.getElementById('close-history-btn');
        const historyGrid = document.getElementById('history-grid');

        historyBtn.addEventListener('click', async () => {
            historyModalOverlay.classList.add('active');
            historyGrid.innerHTML = '<p style="color: rgba(255,255,255,0.5); text-align: center; width: 100%;">Loading videos...</p>';
            
            try {
                const res = await fetch('/get-history');
                const data = await res.json();
                
                if (data.history) {
                    historyGrid.innerHTML = '';
                    if (data.history.length === 0) {
                        historyGrid.innerHTML = '<p style="color: rgba(255,255,255,0.5); text-align: center; width: 100%;">No generated videos found in history.</p>';
                        return;
                    }

                    // Sort by timestamp descending
                    data.history.sort((a, b) => b.timestamp - a.timestamp);

                    data.history.forEach(video => {
                        const dateStr = video.timestamp > 0 ? new Date(video.timestamp * 1000).toLocaleString() : 'Unknown Date';
                        
                        const item = document.createElement('a');
                        item.className = 'history-item glass-list-item';
                        item.href = video.url;
                        item.target = '_blank';
                        
                        item.innerHTML = `
                            <div class="history-info">
                                <span class="history-name">${video.name}</span>
                                <span class="history-date">${dateStr}</span>
                            </div>
                            <div class="history-action">Open &nearr;</div>
                        `;
                        
                        historyGrid.appendChild(item);
                    });
                } else {
                    historyGrid.innerHTML = '<p style="color: #ff4d4d; text-align: center; width: 100%;">Failed to load history.</p>';
                }
            } catch (e) {
                historyGrid.innerHTML = '<p style="color: #ff4d4d; text-align: center; width: 100%;">Error connecting to server.</p>';
            }
        });

        closeHistoryBtn.addEventListener('click', () => {
            historyModalOverlay.classList.remove('active');
        });

        // Close modal on outside click
        historyModalOverlay.addEventListener('click', (e) => {
            if (e.target === historyModalOverlay) {
                closeHistoryBtn.click();
            }
        });
        
        // --- Magnetic Button Hover Effect ---
        document.addEventListener('DOMContentLoaded', () => {
            const buttons = document.querySelectorAll('.neon-btn');
            buttons.forEach(btn => {
                btn.addEventListener('mousemove', (e) => {
                    const rect = btn.getBoundingClientRect();
                    const x = e.clientX - rect.left - rect.width / 2;
                    const y = e.clientY - rect.top - rect.height / 2;
                    // Move the button slightly towards the cursor
                    btn.style.transform = `translate(${x * 0.15}px, ${y * 0.15}px)`;
                });
                btn.addEventListener('mouseleave', () => {
                    // Reset on leave
                    btn.style.transform = 'translate(0px, 0px)';
                });
            });
        });
    