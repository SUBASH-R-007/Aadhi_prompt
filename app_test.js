const window = {};
let currentSlide = 0;
        let conceptMapData = null; // Stores the Skill Tree data

        // Auto-save function to persist edits across reloads
        function saveProjectBackup() {
            try {
                localStorage.setItem('saved_slides_backup', JSON.stringify({
                    subjectName: currentSubjectName,
                    conceptMapData: conceptMapData,
                    slides: slides
                }));
            } catch (e) {
                console.warn("Failed to save project backup to localStorage", e);
            }
        }

        const conceptMapPanel = document.getElementById('concept-map-panel');

        function renderConceptMap(activeConceptId) {
            const canvas = document.getElementById('concept-map-canvas');
            if (!conceptMapData || conceptMapData.length === 0) {
                canvas.innerHTML = `<div style="color: #ff4444; border: 1px solid #ff4444; padding: 1rem; border-radius: 8px; font-size: 0.9rem; margin-top: 1rem;">
                    <strong>[System Debug]</strong><br>
                    Skill tree data is missing! The AI generated an empty concept map for this presentation.<br>
                    Data state: ${conceptMapData === null ? 'null' : (conceptMapData === undefined ? 'undefined' : 'empty array')}
                </div>`;
                return;
            }
            canvas.innerHTML = ''; // Clear

            let passedActive = false;

            conceptMapData.forEach((node) => {
                const nodeDiv = document.createElement('div');
                nodeDiv.className = 'concept-node';
                nodeDiv.textContent = node.title || node.id;
                
                if (node.id === activeConceptId) {
                    nodeDiv.classList.add('active');
                    passedActive = true;
                } else if (!passedActive) {
                    // Nodes before the active one are considered completed
                    nodeDiv.classList.add('completed');
                }
                
                canvas.appendChild(nodeDiv);
            });
        }
        
        // --- Experiential Sound Engine ---
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        const audioCtx = new AudioContext();

        function unlockAudio() {
            if (audioCtx.state === 'suspended') {
                audioCtx.resume();
            }
        }
        // Unlock on any keydown (useful for automated keystroke video renderers like Puppeteer)
        document.addEventListener('keydown', unlockAudio, { once: true });

        function playWhoosh() {
            if (audioCtx.state === 'suspended') return;
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            const filter = audioCtx.createBiquadFilter();
            
            osc.type = 'sine';
            filter.type = 'lowpass';
            
            osc.connect(filter);
            filter.connect(gain);
            gain.connect(audioCtx.destination);
            
            const now = audioCtx.currentTime;
            
            // Frequency sweep for "whoosh"
            osc.frequency.setValueAtTime(800, now);
            osc.frequency.exponentialRampToValueAtTime(100, now + 0.3);
            
            // Filter sweep
            filter.frequency.setValueAtTime(2000, now);
            filter.frequency.exponentialRampToValueAtTime(200, now + 0.3);
            
            // Envelope
            gain.gain.setValueAtTime(0, now);
            gain.gain.linearRampToValueAtTime(0.5, now + 0.1);
            gain.gain.exponentialRampToValueAtTime(0.01, now + 0.4);
            
            osc.start(now);
            osc.stop(now + 0.4);
        }

        function playDing() {
            if (audioCtx.state === 'suspended') return;
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();
            
            osc.type = 'sine';
            
            osc.connect(gain);
            gain.connect(audioCtx.destination);
            
            const now = audioCtx.currentTime;
            
            osc.frequency.setValueAtTime(1200, now);
            
            gain.gain.setValueAtTime(0, now);
            gain.gain.linearRampToValueAtTime(0.3, now + 0.05);
            gain.gain.exponentialRampToValueAtTime(0.01, now + 1.0);
            
            osc.start(now);
            osc.stop(now + 1.0);
        }

        function updateSidePanel(slide) {
            if (window.sidePanelTimeout) {
                clearTimeout(window.sidePanelTimeout);
                window.sidePanelTimeout = null;
            }

            const mascotBg = document.getElementById('mascot-bg');
            const staticBg = document.getElementById('static-bg');
            
            // Hide all side panels first
            document.getElementById('concept-map-panel').classList.remove('active');
            document.getElementById('side-image-panel').classList.remove('active');
            document.getElementById('side-animation-panel').classList.remove('active');
            document.getElementById('side-chart-panel').classList.remove('active');
            document.getElementById('side-3d-panel').classList.remove('active');
            document.getElementById('side-quiz-panel').classList.remove('active');
            document.getElementById('side-terminal-panel').classList.remove('active');
            document.getElementById('side-graph-panel').classList.remove('active');
            document.getElementById('side-video-panel').classList.remove('active');
            
            // 1. Avatar (Mascot) Logic takes absolute priority
            let needsMascot = (slide.type === 'title' || slide.type === 'content');
            if (needsMascot && slide.html && slide.html.length > 300) needsMascot = false;
            if (slide.force_background && slide.force_background !== 'auto') {
                needsMascot = (slide.force_background === 'mascot');
            }

            if (needsMascot) {
                mascotBg.classList.remove('bg-hidden');
                staticBg.classList.add('bg-hidden');
                return; 
            }

            // 2. Avatar is hidden. Show a side panel!
            mascotBg.classList.add('bg-hidden');
            staticBg.classList.remove('bg-hidden');
            
            const flipToPanel = (panelId, preRenderCallback) => {
                if (preRenderCallback) preRenderCallback();
                document.getElementById('concept-map-panel').classList.add('active');
                window.sidePanelTimeout = setTimeout(() => {
                    document.getElementById('concept-map-panel').classList.remove('active');
                    document.getElementById(panelId).classList.add('active');
                }, 1500);
            };

            if (slide.side_panel) {
                const spType = slide.side_panel.type;
                if (spType === 'image') {
                    flipToPanel('side-image-panel', () => {
                        const imgEl = document.getElementById('side-image-display');
                        const loaderEl = document.getElementById('side-image-loader');
                        const prompt = slide.side_panel.prompt || slide.title;
                        
                        imgEl.style.opacity = '0';
                        if(loaderEl) loaderEl.style.opacity = '1';
                        
                        const finalizeImage = (url) => {
                            imgEl.onload = () => {
                                imgEl.style.opacity = '1';
                                if(loaderEl) loaderEl.style.opacity = '0';
                            };
                            imgEl.src = url;
                        };

                        const cachedUrl = imagePreloader.getUrl(prompt);
                        if (cachedUrl) {
                            finalizeImage(cachedUrl);
                        } else {
                            // If we click too fast and beat the preloader, force it to load now
                            imagePreloader.preloadImage(prompt, currentSubjectName)
                                .then(finalizeImage)
                                .catch(() => { if(loaderEl) loaderEl.style.opacity = '0'; });
                        }
                    });
                } else if (spType === 'animation') {
                    flipToPanel('side-animation-panel', () => {
                        spawnSideAnimations(slide.side_panel.keyword || 'math');
                    });
                } else if (spType === 'chart') {
                    flipToPanel('side-chart-panel', () => {
                        renderSideChart(slide.side_panel);
                    });
                } else if (spType === '3d_model') {
                    flipToPanel('side-3d-panel', () => {
                        renderSide3DModel(slide.side_panel.model_name);
                    });
                } else if (spType === 'quiz') {
                    flipToPanel('side-quiz-panel', () => {
                        renderSideQuiz(slide.side_panel);
                    });
                } else if (spType === 'terminal') {
                    flipToPanel('side-terminal-panel', () => {
                        renderSideTerminal(slide.side_panel);
                    });
                } else if (spType === 'graph') {
                    flipToPanel('side-graph-panel', () => {
                        renderSideGraph(slide.side_panel);
                    });
                } else if (spType === 'manim') {
                    flipToPanel('side-video-panel', () => {
                        renderSideVideo(slide.side_panel);
                    });
                } else {
                    document.getElementById('concept-map-panel').classList.add('active');
                }
            } else {
                document.getElementById('concept-map-panel').classList.add('active');
            }
        }

        function spawnSideAnimations(keyword) {
            const canvas = document.getElementById('side-animation-canvas');
            canvas.innerHTML = '';
            
            const isMath = keyword.includes('math') || keyword.includes('science');
            const isCode = keyword.includes('code') || keyword.includes('tech');
            
            // Generate professional geometric floating nodes
            const numNodes = 25;
            for (let i = 0; i < numNodes; i++) {
                const el = document.createElement('div');
                
                // Base styles for professional sleek nodes
                el.style.position = 'absolute';
                el.style.background = 'rgba(255, 215, 0, 0.05)';
                el.style.border = '1px solid rgba(255, 215, 0, 0.3)';
                el.style.boxShadow = '0 0 15px rgba(255, 215, 0, 0.1)';
                
                const size = 10 + Math.random() * 40;
                el.style.width = size + 'px';
                el.style.height = size + 'px';
                el.style.left = Math.random() * 100 + '%';
                el.style.top = (100 + Math.random() * 20) + '%';
                
                // Shape variations based on subject context
                if (isMath) {
                    // Abstract geometric circles
                    el.style.borderRadius = '50%';
                } else if (isCode) {
                    // Sharp tech squares and data blocks
                    el.style.borderRadius = '2px';
                    if (Math.random() > 0.6) el.style.width = (size * 2) + 'px'; 
                } else {
                    // Mixed abstract
                    if (Math.random() > 0.5) el.style.borderRadius = '50%';
                    else el.style.borderRadius = '4px';
                }
                
                canvas.appendChild(el);
                
                if (window.gsap) {
                    gsap.to(el, {
                        y: -1200 - Math.random() * 500,
                        x: "+=" + (Math.random() * 150 - 75),
                        rotation: Math.random() * 360,
                        duration: 20 + Math.random() * 20, // Extremely slow, elegant cinematic drift
                        ease: "linear",
                        repeat: -1,
                        delay: Math.random() * 10,
                        opacity: Math.random() > 0.5 ? 0.8 : 0.2
                    });
                }
            }
        }
        let currentSideChart = null;
        function renderSideChart(config) {
            const canvas = document.getElementById('side-chart-canvas');
            const titleEl = document.getElementById('chart-panel-title');
            if (!canvas) return;
            
            titleEl.innerText = config.title || "Data Visualization";

            if (currentSideChart) {
                currentSideChart.destroy();
            }

            const ctx = canvas.getContext('2d');
            currentSideChart = new Chart(ctx, {
                type: config.chart_type || 'bar',
                data: config.data,
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { labels: { color: 'rgba(255, 255, 255, 0.8)' } }
                    },
                    scales: {
                        x: { ticks: { color: 'rgba(255, 255, 255, 0.6)' }, grid: { color: 'rgba(255, 255, 255, 0.1)' } },
                        y: { ticks: { color: 'rgba(255, 255, 255, 0.6)' }, grid: { color: 'rgba(255, 255, 255, 0.1)' } }
                    }
                }
            });
        }

        let current3DScene = null;
        function renderSide3DModel(modelName) {
            const container = document.getElementById('side-3d-canvas-container');
            container.innerHTML = '';
            
            // Basic Three.js setup
            const scene = new THREE.Scene();
            const camera = new THREE.PerspectiveCamera(75, container.clientWidth / container.clientHeight, 0.1, 1000);
            const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
            renderer.setSize(container.clientWidth, container.clientHeight);
            container.appendChild(renderer.domElement);

            const ambientLight = new THREE.AmbientLight(0x404040);
            scene.add(ambientLight);
            const directionalLight = new THREE.DirectionalLight(0xffd700, 1);
            directionalLight.position.set(1, 1, 1).normalize();
            scene.add(directionalLight);

            let mesh;
            const material = new THREE.MeshPhongMaterial({ color: 0x9b59b6, wireframe: true, emissive: 0x330033 });

            if (modelName === 'torus') {
                const geometry = new THREE.TorusKnotGeometry(10, 3, 100, 16);
                mesh = new THREE.Mesh(geometry, material);
            } else if (modelName === 'atom') {
                const geometry = new THREE.IcosahedronGeometry(15, 1);
                mesh = new THREE.Mesh(geometry, material);
            } else {
                // Default box
                const geometry = new THREE.BoxGeometry(15, 15, 15);
                mesh = new THREE.Mesh(geometry, material);
            }

            scene.add(mesh);
            camera.position.z = 30;

            let animationId;
            const animate = function () {
                animationId = requestAnimationFrame(animate);
                mesh.rotation.x += 0.005;
                mesh.rotation.y += 0.01;
                renderer.render(scene, camera);
            };
            animate();
            current3DScene = { renderer, animationId };
        }

        function renderSideQuiz(config) {
            document.getElementById('quiz-question-text').innerText = config.question || "Question?";
            const optionsContainer = document.getElementById('quiz-options-container');
            const feedbackEl = document.getElementById('quiz-feedback');
            optionsContainer.innerHTML = '';
            feedbackEl.style.opacity = '0';

            const options = config.options || ["True", "False"];
            const correctIndex = config.correct_index || 0;

            options.forEach((opt, idx) => {
                const btn = document.createElement('button');
                btn.innerText = opt;
                btn.style.padding = "1rem";
                btn.style.background = "rgba(0,0,0,0.5)";
                btn.style.border = "1px solid rgba(255,215,0,0.3)";
                btn.style.color = "white";
                btn.style.borderRadius = "10px";
                btn.style.cursor = "pointer";
                btn.style.transition = "all 0.2s";
                btn.style.fontSize = "1rem";
                
                btn.onmouseover = () => btn.style.background = "rgba(176,38,255,0.4)";
                btn.onmouseout = () => btn.style.background = "rgba(0,0,0,0.5)";

                btn.onclick = () => {
                    if (idx === correctIndex) {
                        btn.style.background = "rgba(40, 200, 40, 0.6)";
                        btn.style.borderColor = "rgba(40, 255, 40, 1)";
                        feedbackEl.innerText = "Correct! ✨";
                        feedbackEl.style.color = "#4caf50";
                        feedbackEl.style.opacity = '1';
                    } else {
                        btn.style.background = "rgba(200, 40, 40, 0.6)";
                        btn.style.borderColor = "rgba(255, 40, 40, 1)";
                        feedbackEl.innerText = "Incorrect. Try again.";
                        feedbackEl.style.color = "#f44336";
                        feedbackEl.style.opacity = '1';
                    }
                };
                optionsContainer.appendChild(btn);
            });

            // Auto-reveal for video export workflow
            if (window.currentQuizTimeout) clearTimeout(window.currentQuizTimeout);
            window.currentQuizTimeout = setTimeout(() => {
                const correctBtn = optionsContainer.children[correctIndex];
                if (correctBtn && feedbackEl.style.opacity === '0') {
                    playDing();
                    correctBtn.style.background = "rgba(40, 200, 40, 0.6)";
                    correctBtn.style.borderColor = "rgba(40, 255, 40, 1)";
                    feedbackEl.innerText = "Correct! ✨";
                    feedbackEl.style.color = "#4caf50";
                    feedbackEl.style.opacity = '1';
                }
            }, 4000);
        }

        function renderSideTerminal(config) {
            const outEl = document.getElementById('side-terminal-output');
            outEl.innerText = "root@aadhi:~# " + (config.command || "executing...");
            
            // Simple typing effect
            const fullText = "\n\n" + (config.output || "Process completed successfully.\nMemory cleared.");
            let i = 0;
            const typeWriter = () => {
                if (i < fullText.length) {
                    outEl.innerHTML += fullText.charAt(i);
                    i++;
                    setTimeout(typeWriter, 20); // typing speed
                }
            };
            setTimeout(typeWriter, 500);
        }

        let currentJSXBoard = null;
        function renderSideGraph(config) {
            document.getElementById('graph-panel-title').innerText = config.title || "Interactive Graph";
            if (currentJSXBoard) {
                JXG.JSXGraph.freeBoard(currentJSXBoard);
            }
            
            currentJSXBoard = JXG.JSXGraph.initBoard('side-jxgbox', {
                boundingbox: [-5, 5, 5, -5],
                axis: true,
                showCopyright: false,
                theme: 'dark'
            });
            
            if (config.function) {
                // Safely plot the function if provided
                try {
                    currentJSXBoard.create('functiongraph', [function(x){
                        // A simple eval for demo purposes. In production, use a math parser.
                        return eval(config.function.replace(/x/g, `(${x})`));
                    }], {strokeColor: '#b026ff', strokeWidth: 3});
                } catch(e) {
                    console.error("Function parsing error:", e);
                }
            }
        }

        function renderSideVideo(config) {
            const videoElement = document.getElementById('side-video-player');
            const labelElement = document.getElementById('side-video-label');
            
            if (config.video_url) {
                videoElement.src = config.video_url;
                videoElement.play().catch(e => console.log("Auto-play prevented", e));
            }
            if (config.title) {
                labelElement.innerText = config.title;
            } else {
                labelElement.innerText = "Explanatory Video";
            }
        }


        window.currentSlideRenderId = 0;

        // --- WebSocket Live Progress ---
        let wsProgress;
        function connectWebSocket() {
            wsProgress = new WebSocket(`ws://${window.location.host}/ws/progress`);
            wsProgress.onmessage = function(event) {
                if (event.data.startsWith('MANIM_LOG:')) {
                    const logMsg = event.data.replace('MANIM_LOG:', '');
                    const logDiv = document.querySelector('.manim-live-log');
                    if (logDiv) {
                        // Keep it on one line but replace text
                        logDiv.textContent = logMsg.substring(0, 80) + (logMsg.length > 80 ? '...' : '');
                    }
                }
            };
            wsProgress.onclose = function() {
                setTimeout(connectWebSocket, 5000); // Reconnect
            };
        }
        connectWebSocket();
        // -------------------------------

        function renderSlide(index) {
            const thisSlideRenderId = ++window.currentSlideRenderId;
            
            // Instantly kill any currently playing or fetching TTS audio from previous slides
            speakNarration(null);
            
            const slide = slides[index];
            const container = document.getElementById('slide-content-container');
            
            // Slide Scrubber Logic
            const scrubber = document.getElementById('slide-scrubber-container');
            if (scrubber) {
                if (scrubber.children.length !== slides.length) {
                    scrubber.innerHTML = '';
                    slides.forEach((s, idx) => {
                        const dot = document.createElement('div');
                        dot.className = 'scrubber-dot';
                        if (idx === index) dot.classList.add('active');
                        
                        const tooltip = document.createElement('div');
                        tooltip.className = 'scrubber-tooltip';
                        tooltip.textContent = s.title || `Scene ${idx + 1}`;
                        dot.appendChild(tooltip);
                        
                        dot.addEventListener('click', () => {
                            if (currentSlide !== idx) {
                                currentSlide = idx;
                                renderSlide(idx);
                            }
                        });
                        scrubber.appendChild(dot);
                    });
                    scrubber.classList.add('visible');
                } else {
                    // Just update active state
                    Array.from(scrubber.children).forEach((dot, idx) => {
                        if (idx === index) dot.classList.add('active');
                        else dot.classList.remove('active');
                    });
                }
            }
            const visualContainer = document.getElementById('slide-visual-container');
            const titleContainer = document.getElementById('slide-title-container');
            const board = document.getElementById('presentation-board');

            // --- Background Transition Engine ---
            if (slide.type === 'ai_video' || slide.type === 'simulation' || slide.type === 'p5_simulation') {
                // We are in full screen. Safe to swap background underneath for the next slide!
                let nextContentSlide = null;
                for (let i = index + 1; i < slides.length; i++) {
                    if (slides[i].type !== 'ai_video' && slides[i].type !== 'simulation' && slides[i].type !== 'p5_simulation') {
                        nextContentSlide = slides[i];
                        break;
                    }
                }
                if (nextContentSlide) {
                    updateSidePanel(nextContentSlide);
                }
            } else {
                // Not in full screen, just render this slide's side panel normally
                updateSidePanel(slide);
            }
            // If NOT a full screen video, we DO NOT swap backgrounds (preserving continuity).

            // Update Progress Bar
            const progress = (index / (slides.length - 1)) * 100;
            document.getElementById('progress-bar').style.width = `${progress}%`;

            // Update Concept Map
            if (slide.concept_id) {
                renderConceptMap(slide.concept_id);
            }

            const isGoingFullscreen = (slide.type === 'ai_video' || slide.type === 'visual' || slide.type === 'simulation');
            const wasFullscreen = board.classList.contains('cinematic-fullscreen');

            const performDOMUpdate = () => {
                if (isGoingFullscreen) {
                    board.style.height = ''; // Ensure !important 100vh works
                } else {
                    board.style.height = 'fit-content';
                }

                // Remove old classes
                container.className = 'slide-container slide-content';
                titleContainer.innerHTML = '';
                
                // Ensure visibility (no manual fading needed with View Transitions)
                titleContainer.style.opacity = '1';
                container.style.opacity = '1';
                if (visualContainer) visualContainer.style.opacity = '1';
                board.classList.add('visible');
                
                // Handle Visual Container Layout
                if (visualContainer) {
                    // Explicitly pause and wipe any active videos to guarantee audio stops instantly
                    const activeVideos = visualContainer.querySelectorAll('video');
                    activeVideos.forEach(v => {
                        v.pause();
                        v.removeAttribute('src');
                        v.load();
                    });
                    
                    visualContainer.innerHTML = '';
                    if (slide.type === 'ai_video') {
                        let prompt = slide.prompt;
                        visualContainer.classList.remove('hidden');
                        container.classList.add('hidden'); // Hide text container completely

                        if (prompt) {
                            visualContainer.innerHTML = `<div id="jxgbox" class="jxgbox" style="width:100%; height:100%; border-radius:0px; overflow:hidden; display:flex; justify-content:center; align-items:center; flex-direction:column; background:#000;">
                                <div class="spinner" style="border-top-color: #FFD700;"></div>
                                <div style="margin-top:1rem; color:#FFD700; font-family:monospace; text-align:center;">Generating AI Video (approx 1-2 min)...<br><span style="font-size:0.8em; opacity:0.8;">Prompt: "${prompt}"</span></div>
                            </div>`;
                            
                            fetch('/generate-ai-video', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({prompt: prompt})
                            }).then(res => {
                                if (!res.ok) return res.json().then(err => {throw new Error(err.detail)});
                                return res.json();
                            }).then(data => {
                                if (window.currentSlideRenderId !== thisSlideRenderId) return;
                                if(data.status === 'success') {
                                    let videoAttrs = slide.ai_audio_source === 'video' 
                                        ? 'autoplay style="width:100%; height:100%; object-fit:cover;"' 
                                        : 'autoplay loop muted style="width:100%; height:100%; object-fit:cover;"';
                                    
                                    document.getElementById('jxgbox').innerHTML = `<video id="active-ai-video" src="${data.video_url}" ${videoAttrs}></video>`;

                                    if (slide.ai_audio_source === 'video') {
                                        const videoEl = document.getElementById('active-ai-video');
                                        videoEl.onended = () => {
                                            if (ttsState.isPlaying) {
                                                if (currentSlide < slides.length - 1) {
                                                    currentSlide++;
                                                    setTimeout(() => {
                                                        if (ttsState.isPlaying) renderSlide(currentSlide);
                                                    }, 800);
                                                } else {
                                                    ttsState.isPlaying = false;
                                                    updateTTSButtons();
                                                    if (isAutoExporting && autoExportRecorder && autoExportRecorder.state === 'recording') {
                                                        autoExportRecorder.stop();
                                                    }
                                                }
                                            }
                                        };
                                    }
                                } else if(data.status === 'manual_required') {
                                    document.getElementById('jxgbox').innerHTML = `
                                    <div style='color:var(--border-gold); padding:2rem; font-family:sans-serif; text-align:center; height:100%; display:flex; flex-direction:column; justify-content:center; align-items:center; max-width:800px; margin:0 auto;'>
                                        <h2 style='font-size:2rem; margin-bottom:1rem; color: #E50914;'>Manual AI Generation Required</h2>
                                        <p style='font-size:1.1rem; color:#ccc; margin-bottom:2rem;'>You selected <b>Option 3 (No AI Generation)</b>. You must generate this video externally and place it in the server folder to continue.</p>
                                        <div style='background:rgba(255,255,255,0.1); padding:1.5rem; border-radius:8px; width:100%; text-align:left; margin-bottom:1.5rem;'>
                                            <div style='font-size:0.9rem; color:#aaa; margin-bottom:0.5rem;'>Requested Prompt:</div>
                                            <div style='font-size:1.1rem; color:#fff;'>${data.prompt}</div>
                                        </div>
                                        <div style='background:rgba(255,255,255,0.1); padding:1.5rem; border-radius:8px; width:100%; text-align:left; margin-bottom:2rem;'>
                                            <div style='font-size:0.9rem; color:#aaa; margin-bottom:0.5rem;'>Generate your video (704p or 720p at 25fps) and save it exactly as:</div>
                                            <div style='font-size:1.3rem; font-family:monospace; color:#4CAF50;'>static_videos/${data.filename}</div>
                                        </div>
                                        <button onclick='renderSlide(currentSlide)' class='btn-gold' style='padding:1rem 2rem; font-size:1.1rem; cursor:pointer;'>I have placed the file, click to reload scene</button>
                                    </div>`;
                                } else {
                                    throw new Error(data.detail || 'Unknown AI video error');
                                }
                            }).catch(err => {
                                if (window.currentSlideRenderId !== thisSlideRenderId) return;
                                const jxgbox = document.getElementById('jxgbox');
                                if(jxgbox) {
                                    jxgbox.innerHTML = `<div style='color:#FF4444; padding:1rem; font-family:monospace; font-size:12px; overflow-y:auto; max-height:100%; width:100%; text-align:left;'><b>AI Video Error:</b><br>${err.message}</div>`;
                                }
                            });
                        } else {
                            visualContainer.innerHTML = `<div style='color:#FF4444; padding:1rem; font-family:monospace; font-size:12px; overflow-y:auto; max-height:100%; width:100%; text-align:left;'><b>Error:</b> The AI failed to generate a 'prompt' for this AI video scene.</div>`;
                        }
                    } else if (slide.type === 'p5_simulation') {
                        visualContainer.classList.remove('hidden');
                        container.classList.add('hidden'); // Hide text container
                        
                        visualContainer.innerHTML = '';
                        
                        const iframe = document.createElement('iframe');
                        iframe.style.width = '100%';
                        iframe.style.height = '100%';
                        iframe.style.border = 'none';
                        iframe.style.borderRadius = '12px';
                        
                        // Construct HTML blob for iframe
                        const p5Html = `
                            <!DOCTYPE html>
                            <html>
                            <head>
                                <script src="https://cdnjs.cloudflare.com/ajax/libs/p5.js/1.9.0/p5.min.js"><\/script>
                                <style>
                                    body { margin: 0; padding: 0; overflow: hidden; display: flex; justify-content: center; align-items: center; background: #000; }
                                    canvas { box-shadow: 0 0 20px rgba(255, 215, 0, 0.2); border-radius: 8px; }
                                </style>
                            </head>
                            <body>
                                <script>
                                    ${slide.p5_code || '// No p5 code provided'}
                                <\/script>
                                <script src="app.js" defer><\/script>
</body>
                            </html>
                        `;
                        
                        const blob = new Blob([p5Html], {type: 'text/html'});
                        iframe.src = URL.createObjectURL(blob);
                        visualContainer.appendChild(iframe);
                        
                    } else if (slide.type === 'visual' || slide.type === 'simulation') {
                        let code = slide.manim_code || slide.simulation_code || slide.code;
                        let svg = slide.visual_svg || slide.svg;
                        
                        visualContainer.classList.remove('hidden');
                        container.classList.add('hidden'); // Hide text container completely

                        if (code) {
                            visualContainer.innerHTML = `<div id="jxgbox" class="jxgbox" style="width:100%; height:100%; border-radius:0px; overflow:hidden; display:flex; justify-content:center; align-items:center; flex-direction:column; background:#000;">
                                <div class="spinner" style="border-top-color: #FFD700;"></div>
                                <div style="margin-top:1rem; color:#FFD700; font-family:monospace;">Compiling Manim Video...</div>
                                <div class="manim-live-log" style="margin-top:0.5rem; color:#888; font-family:monospace; font-size:12px; max-width:80%; text-align:center;">Connecting to build server...</div>
                            </div>`;
                            
                            // Call Python backend
                            fetch('/render', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({code: code})
                            }).then(res => {
                                if (!res.ok) return res.json().then(err => {throw new Error(err.detail)});
                                return res.json();
                            }).then(data => {
                                if (window.currentSlideRenderId !== thisSlideRenderId) return;
                                if(data.status === 'success') {
                                    document.getElementById('jxgbox').innerHTML = `<video src="${data.video_url}" autoplay loop muted style="width:100%; height:100%; object-fit:cover;"></video>`;
                                } else {
                                    throw new Error(data.detail || 'Unknown compilation error');
                                }
                            }).catch(err => {
                                if (window.currentSlideRenderId !== thisSlideRenderId) return;
                                const jxgbox = document.getElementById('jxgbox');
                                if(jxgbox) {
                                    jxgbox.innerHTML = `<div style='color:#FF4444; padding:1rem; font-family:monospace; font-size:12px; overflow-y:auto; max-height:100%; width:100%; text-align:left;'><b>Manim Compilation Error:</b><br>${err.message}<br><br><b>Code Generated:</b><br><pre style="white-space:pre-wrap; word-break:break-all;">${code.replace(/</g, '&lt;')}</pre></div>`;
                                }
                            });
                        } else if (svg) {
                            visualContainer.innerHTML = svg;
                        } else {
                            visualContainer.innerHTML = `<div style='color:#FF4444; padding:1rem; font-family:monospace; font-size:12px; overflow-y:auto; max-height:100%; width:100%; text-align:left;'><b>Error:</b> The AI failed to generate 'manim_code' for this scene.<br><br><b>Raw Slide Object Generated:</b><br><pre style="white-space:pre-wrap; word-break:break-all;">${JSON.stringify(slide, null, 2).replace(/</g, '&lt;')}</pre></div>`;
                        }
                    } else {
                        visualContainer.classList.add('hidden');
                        container.classList.remove('hidden'); // Show text container
                    }
                }

                if (slide.type === 'title') {
                    container.classList.add('slide-type-title');
                    container.innerHTML = `
                        <div class="title-slide">
                            <h1 class="main-title">${slide.title}</h1>
                            ${slide.subtitle ? `<h2>${slide.subtitle}</h2>` : ''}
                        </div>
                    `;
                } else if (slide.type === 'visual' || slide.type === 'simulation' || slide.type === 'ai_video') {
                    // For visual, simulation, and ai_video slides, just set the title above
                    let titleHTML = `<h1 class="slide-title">${slide.title}</h1>`;
                    if (slide.subtitle) {
                        titleHTML += `<h2 class="section-heading" style="margin-top:-1rem; margin-bottom: 1.5rem;">${slide.subtitle}</h2>`;
                    }
                    titleContainer.innerHTML = titleHTML;
                    container.innerHTML = ''; // Ensure text container is empty
                } else {
                    if (slide.type === 'example') {
                        container.classList.add('slide-type-example');
                    }

                    let titleHTML = `<h1 class="slide-title">${slide.title}</h1>`;
                    if (slide.subtitle) {
                        titleHTML += `<h2 class="section-heading" style="margin-top:-1rem; margin-bottom: 1.5rem;">${slide.subtitle}</h2>`;
                    }
                    titleContainer.innerHTML = titleHTML;

                    let finalHtml = slide.html || '';
                    if (finalHtml && slide.uploaded_images) {
                        const tempDiv = document.createElement('div');
                        tempDiv.innerHTML = finalHtml;
                        const placeholders = tempDiv.querySelectorAll('.board-image-placeholder');
                        placeholders.forEach(ph => {
                            const imgId = ph.getAttribute('data-img-id');
                            if (slide.uploaded_images[imgId]) {
                                const img = document.createElement('img');
                                img.src = slide.uploaded_images[imgId];
                                img.style.float = 'right';
                                img.style.maxWidth = '45%';
                                img.style.maxHeight = '350px';
                                img.style.borderRadius = '12px';
                                img.style.objectFit = 'contain';
                                img.style.display = 'block';
                                img.style.margin = '0.5rem 0 1rem 2rem';
                                img.style.boxShadow = '0 0 20px rgba(255,215,0,0.2)';
                                img.style.clear = 'right';
                                ph.replaceWith(img);
                            }
                        });
                        finalHtml = tempDiv.innerHTML;
                    }
                    container.innerHTML = finalHtml;
                    
                    // Process Left Pane Dynamic GIFs
                    const liveGifs = container.querySelectorAll('.context-gif');
                    liveGifs.forEach(gif => {
                        const query = gif.getAttribute('data-query');
                        if (query) {
                            fetch(`https://api.giphy.com/v1/gifs/random?api_key=GlVGYHqc3SyZxHpzCW9Z2iHk1D3rA56Y&tag=${encodeURIComponent(query)}&rating=pg-13`)
                                .then(res => res.json())
                                .then(data => {
                                    if (data.data && data.data.images && data.data.images.downsized_medium) {
                                        gif.src = data.data.images.downsized_medium.url;
                                    } else {
                                        gif.style.display = 'none';
                                    }
                                })
                                .catch(e => {
                                    console.log('Giphy fetch error', e);
                                    gif.style.display = 'none';
                                });
                        }
                    });

                    // Process Syntax Highlighting for Left Pane Code Blocks
                    if (window.Prism) {
                        Prism.highlightAllUnder(container);
                    }
                }

                if (isGoingFullscreen) {
                    board.classList.add('cinematic-fullscreen');
                    document.body.classList.remove('text-mode-active');
                } else {
                    board.classList.remove('cinematic-fullscreen');
                    document.body.classList.add('text-mode-active');
                }
            }; // End of performDOMUpdate

            const finalizeSlideAnimations = () => {
                // 1. Cinematic Zoom Trigger
                const overlayZone = document.querySelector('.lecture-overlay-zone');
                const dynamicZone = document.querySelector('.dynamic-side-zone');
                if (overlayZone && dynamicZone) {
                    overlayZone.classList.remove('cinematic-zoom-active');
                    dynamicZone.classList.remove('cinematic-zoom-active');
                    
                    if (!isGoingFullscreen) {
                        void overlayZone.offsetWidth; // trigger reflow to restart animation
                        overlayZone.classList.add('cinematic-zoom-active');
                        dynamicZone.classList.add('cinematic-zoom-active');
                    }
                }

                // 2. Kinetic Typography (Staggered cascading reveal)
                const animateItems = container.querySelectorAll('h2, h3, p, li, tbody tr, .math-block, .definition, .formula-block, .info-callout, .warning-callout, .tip-callout, pre, .context-gif');
                
                animateItems.forEach((item, i) => {
                    // Start hidden, waiting for TTS sync
                    item.classList.add('cascade-hidden');
                    item.classList.remove('animate-item'); // cleanup old legacy class
                    item.classList.remove('cascade-visible');
                });

                // Re-trigger MathJax
                if (window.MathJax && MathJax.typesetPromise) {
                    MathJax.typesetPromise([container]).catch((err) => console.log('MathJax error:', err));
                }
                
                // Re-trigger Prism JS Syntax Highlighting
                if (window.Prism) {
                    Prism.highlightAllUnder(container);
                }

                // Speech narration trigger
                if (slide.type === 'ai_video' && slide.ai_audio_source === 'video') {
                    // Video plays natively with audio, rely on its onended listener for auto-advance.
                } else if (slide.narration) {
                    speakNarration(slide.narration, Array.from(animateItems));
                } else {
                    // Fallback reveal if no TTS or if narration is missing
                    animateItems.forEach((item, i) => {
                        setTimeout(() => {
                            item.classList.remove('cascade-hidden');
                            item.classList.add('cascade-visible');
                        }, i * 400 + 200); // Fast cascade fallback
                    });
                    
                    updateTTSButtons();
                    
                    // Fallback auto-advance for silent slides if exporting
                    if (isAutoExporting) {
                        setTimeout(() => {
                            if (currentSlide < slides.length - 1) {
                                currentSlide++;
                                renderSlide(currentSlide);
                            } else {
                                if (autoExportRecorder && autoExportRecorder.state === 'recording') {
                                    autoExportRecorder.stop();
                                }
                            }
                        }, 5000); // 5 sec fallback for silent slides
                    }
                }
            }; // End of finalizeSlideAnimations

            // Execute using Native GPU View Transitions for buttery smooth 120fps morphs
            if (document.startViewTransition) {
                try {
                    const transition = document.startViewTransition(() => {
                        performDOMUpdate();
                    });
                    transition.ready.then(() => {
                        finalizeSlideAnimations();
                    }).catch(() => {
                        finalizeSlideAnimations();
                    });
                } catch (e) {
                    console.warn("View transition skipped:", e);
                    performDOMUpdate();
                    finalizeSlideAnimations();
                }
            } else {
                performDOMUpdate();
                finalizeSlideAnimations();
            }
        }

        document.addEventListener('keydown', (e) => {
            const scriptModal = document.getElementById('script-editor-modal');
            const infoModal = document.getElementById('info-modal');
            
            // Do not trigger slide transitions if a modal is open or typing in an input
            if (scriptModal && scriptModal.classList.contains('active')) return;
            if (infoModal && infoModal.classList.contains('active')) return;
            if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;

            if (e.key === 'ArrowRight') {
                e.preventDefault();
                nextSlide();
            } else if (e.key === 'ArrowLeft') {
                e.preventDefault();
                prevSlide();
            } else if (e.key === ' ' || e.key === 'Spacebar') {
                e.preventDefault();
                nextSlide();
            }
        });

        function nextSlide() {
            if (currentSlide < slides.length - 1) {
                currentSlide++;
                renderSlide(currentSlide);
            }
        }

        function prevSlide() {
            if (currentSlide > 0) {
                currentSlide--;
                renderSlide(currentSlide);
            }
        }

        // --- Start Lecture Overlay Logic ---
        const startOverlay = document.getElementById('start-overlay');
        const startBtn = document.getElementById('start-lecture-btn');
        const uploadScreen = document.getElementById('upload-screen');
        const presentationBoard = document.getElementById('presentation-board');

        function showStartOverlay() {
            uploadScreen.classList.add('hidden');
            
            // Start background preloading of all AI images to prevent rate-limits and lag
            slides.forEach(s => {
                if (s.side_panel && s.side_panel.type === 'image') {
                    const prompt = s.side_panel.prompt || s.title;
                    imagePreloader.enqueue(prompt, currentSubjectName);
                }
            });
            
            // Bypass the start overlay and initialize audio context immediately
            const dummyAudio = new Audio();
            dummyAudio.play().catch(e => {});
            
            startPresentationSequence(false);
        }

        // --- Start Presentation Flow ---
        let introTimeout1, introTimeout2, introTimeout3;
        let isIntroRunning = false;
        let currentIsExporting = false;

        function introSkipListener(e) {
            if (e.key === 'ArrowRight' || e.key === ' ' || e.key === 'Spacebar') {
                e.preventDefault();
                skipIntroSequence();
            }
        }

        function skipIntroSequence() {
            if (!isIntroRunning) return;
            clearTimeout(introTimeout1);
            clearTimeout(introTimeout2);
            clearTimeout(introTimeout3);
            
            document.removeEventListener('keydown', introSkipListener);
            isIntroRunning = false;
            
            const introContainer = document.getElementById('intro-sequence-container');
            const logoVideo = document.getElementById('intro-logo-video');
            
            logoVideo.pause();
            introContainer.style.display = 'none';
            
            startMainPresentation();
        }

        function startMainPresentation() {
            const presentationBoard = document.getElementById('presentation-board');
            presentationBoard.classList.remove('hidden');
            document.body.classList.add('presentation-active');
            ttsState.isPlaying = true;

            // Set initial background state based on the first slide
            if (slides.length > 0) {
                updateSidePanel(slides[0]);
            }

            if (!currentIsExporting) updateTTSButtons();
            renderSlide(currentSlide);
        }

        function startPresentationSequence(isExporting) {
            currentIsExporting = isExporting;
            if (window.currentAudio) window.currentAudio.pause();
            currentSlide = 0;
            
            // Mark presentation as active to show bottom bar immediately
            document.body.classList.add('presentation-active');
            
            // Hide presentation board initially
            const presentationBoard = document.getElementById('presentation-board');
            presentationBoard.classList.add('hidden');
            
            const introContainer = document.getElementById('intro-sequence-container');
            const logoVideo = document.getElementById('intro-logo-video');
            const subjectTitle = document.getElementById('intro-subject-title');
            const staticBg = document.getElementById('intro-static-bg');
            
            introContainer.style.display = 'flex';
            logoVideo.style.display = 'block';
            subjectTitle.style.display = 'none';
            subjectTitle.style.opacity = '0';
            staticBg.style.display = 'none';
            
            logoVideo.currentTime = 0;
            logoVideo.play().catch(e => console.error("Logo video play error", e));
            
            isIntroRunning = true;
            document.addEventListener('keydown', introSkipListener);
            
            // Force transition to subject title exactly after 5.5 seconds
            introTimeout1 = setTimeout(() => {
                if (!isIntroRunning) return;
                logoVideo.pause();
                logoVideo.style.display = 'none';
                
                // Show static background and subject title
                staticBg.style.display = 'block';
                subjectTitle.innerText = currentSubjectName || "Subject Title";
                subjectTitle.style.display = 'block';
                // Trigger fade in
                setTimeout(() => { subjectTitle.style.opacity = '1'; }, 100);
                
                // Hold for 3 seconds, then fade out and start presentation
                introTimeout2 = setTimeout(() => {
                    if (!isIntroRunning) return;
                    subjectTitle.style.opacity = '0';
                    introTimeout3 = setTimeout(() => {
                        if (!isIntroRunning) return;
                        document.removeEventListener('keydown', introSkipListener);
                        isIntroRunning = false;
                        introContainer.style.display = 'none';
                        startMainPresentation();
                    }, 2000); // Wait for fade out
                }, 3000); // Hold text for 3 seconds
            }, 5500); // Wait exactly 5.5 seconds for logo video
        }

        startBtn.addEventListener('click', () => {
            // Unlocks Audio Context
            const dummyAudio = new Audio();
            dummyAudio.play().catch(e => {});
            
            startOverlay.style.opacity = '0';
            setTimeout(() => {
                startOverlay.style.display = 'none';
                startPresentationSequence(false);
            }, 500);
        });

        // Setup PDF.js worker
        if (typeof pdfjsLib !== 'undefined') {
            pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
        }

        // --- Image Preloader Engine ---
        class ImagePreloaderQueue {
            constructor() {
                this.queue = [];
                this.cache = new Map();
                this.activeConnections = 0;
                this.maxConnections = 2; // Strict limit to prevent Pollinations 429 Rate Limits
            }

            enqueue(prompt, subjectName) {
                if (!prompt) return;
                const cacheKey = prompt.trim();
                if (this.cache.has(cacheKey) || this.queue.some(q => q.prompt === cacheKey)) return;
                
                this.queue.push({ prompt: cacheKey, subjectName });
                this.processQueue();
            }

            async processQueue() {
                if (this.activeConnections >= this.maxConnections || this.queue.length === 0) return;
                
                const item = this.queue.shift();
                this.activeConnections++;
                
                try {
                    await this.preloadImage(item.prompt, item.subjectName);
                } catch (e) {
                    console.error("Failed to preload:", item.prompt);
                } finally {
                    this.activeConnections--;
                    this.processQueue(); // Fetch next
                }
            }

            preloadImage(prompt, subjectName) {
                return new Promise((resolve, reject) => {
                    const img = new Image();
                    const encoded = encodeURIComponent(prompt);
                    const encodedSubject = encodeURIComponent(subjectName || "");
                    const url = `/get-image?prompt=${encoded}&subjectName=${encodedSubject}`;
                    
                    img.onload = () => {
                        this.cache.set(prompt, url);
                        resolve(url);
                    };
                    img.onerror = reject;
                    img.src = url;
                });
            }

            getUrl(prompt) {
                return this.cache.get(prompt ? prompt.trim() : "");
            }
        }
        
        const imagePreloader = new ImagePreloaderQueue();

        // State variables
        let selectedFile = null;
        let currentSubjectName = "";

        // Elements
        const modelSelect = document.getElementById('model-select');
        const dropZone = document.getElementById('drop-zone');
        const dropZoneText = document.getElementById('drop-zone-text');
        const fileInput = document.getElementById('file-input');
        const browseBtn = document.getElementById('browse-btn');
        const generateBtn = document.getElementById('generate-btn');
        const loadingOverlay = document.getElementById('loading-overlay');
        const loadingTitle = document.getElementById('loading-title');
        const loadingLog = document.getElementById('loading-log');

        // Trigger file input click when clicking drop zone or browse button
        dropZone.addEventListener('click', () => fileInput.click());
        browseBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            fileInput.click();
        });

        // Handle File Selection
        fileInput.addEventListener('change', (e) => {
            handleFiles(e.target.files);
        });

        // Drag and Drop Events
        ['dragenter', 'dragover'].forEach(eventName => {
            dropZone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropZone.classList.add('dragover');
            }, false);
        });

        ['dragleave', 'drop'].forEach(eventName => {
            dropZone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropZone.classList.remove('dragover');
            }, false);
        });

        dropZone.addEventListener('drop', (e) => {
            const dt = e.dataTransfer;
            const files = dt.files;
            handleFiles(files);
        });

        function handleFiles(files) {
            if (files.length === 0) return;
            const file = files[0];
            const isPDF = file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf');
            const isDOCX = file.name.toLowerCase().endsWith('.docx') || file.type.includes('wordprocessingml');
            
            if (!isPDF && !isDOCX) {
                alert('Please upload a PDF or DOCX file.');
                return;
            }
            selectedFile = file;
            const sizeMB = (file.size / (1024 * 1024)).toFixed(2);
            dropZoneText.innerHTML = `<strong>Selected:</strong> ${file.name} (${sizeMB} MB) <span style="color:var(--text-gold);">✓</span>`;
            generateBtn.disabled = false;
        }

        // Log printing helper
        function logStatus(title, message) {
            loadingTitle.textContent = title;
            loadingLog.textContent += `[${new Date().toLocaleTimeString()}] ${message}\n`;
            loadingLog.scrollTop = loadingLog.scrollHeight;
        }

        // Robust JSON parse with advanced backslash repair
        function robustParseJSON(text) {
            try {
                return JSON.parse(text);
            } catch (e) {
                console.warn("Standard JSON.parse failed, attempting repair...", e);
                
                // Fix unescaped literal control characters
                let repaired = text.replace(/(?<!\\)\n/g, '\\n')
                                   .replace(/(?<!\\)\r/g, '\\r')
                                   .replace(/(?<!\\)\t/g, '\\t');
                
                // Fix invalid backslash escapes (e.g. \s, \a, \ ) by pairing odd backslashes
                repaired = repaired.replace(/\\+/g, function(match, offset, str) {
                    if (match.length % 2 === 0) return match;
                    
                    const nextChar = str[offset + match.length];
                    const isHex = nextChar === 'u' && /^[0-9a-fA-F]{4}/.test(str.substring(offset + match.length + 1, offset + match.length + 5));
                    const isValidJSONEscape = ['"', '/', 'b', 'f', 'n', 'r', 't'].includes(nextChar) || isHex;
                    
                    if (isValidJSONEscape) return match;
                    return match + '\\'; // Make the sequence even, thereby escaping the last backslash
                });
                
                // Fix trailing commas in arrays/objects
                repaired = repaired.replace(/,\s*([}\]])/g, '$1');
                
                return JSON.parse(repaired);
            }
        }

        // TTS Controller State
        const ttsState = {
            isPlaying: true, // Auto-play by default
            isMuted: false,
            rate: 0.9,
            pitch: 1.0,
            currentUtterance: null
        };

        // TTS Control Elements
        const ttsPlayBtn = document.getElementById('tts-play-btn');
        const ttsStopBtn = document.getElementById('tts-stop-btn');
        const ttsMuteBtn = document.getElementById('tts-mute-btn');
        const ttsRateSlider = document.getElementById('tts-rate-slider');
        const ttsRateValue = document.getElementById('tts-rate-value');

        // Prevent keyboard events inside controls from bubbling to slide navigation
        [ttsPlayBtn, ttsStopBtn, ttsMuteBtn, ttsRateSlider].forEach(element => {
            if (element) {
                element.addEventListener('keydown', (e) => {
                    e.stopPropagation();
                });
            }
        });

        // Initialize voices list
        window.speechSynthesis.onvoiceschanged = () => {
            // Triggered when voices are loaded by browser
        };

        window.currentAudio = null;
        window.currentNarrationId = 0;

        async function speakNarration(text, syncElements = []) {
            const thisNarrationId = ++window.currentNarrationId;

            const triggerFallbackReveal = () => {
                if (syncElements && syncElements.length > 0) {
                    syncElements.forEach((el, index) => {
                        setTimeout(() => {
                            el.classList.remove('cascade-hidden');
                            el.classList.add('cascade-visible');
                        }, index * 400 + 200);
                    });
                }
            };

            if (window.currentAudio) {
                window.currentAudio.pause();
                if (window.currentAudio.parentNode) {
                    window.currentAudio.parentNode.removeChild(window.currentAudio);
                }
                window.currentAudio = null;
            }
            if (window.speechSynthesis.speaking) {
                window.speechSynthesis.cancel();
            }

            if (ttsState.isMuted || !text) {
                triggerFallbackReveal();
                return;
            }

            const ttsEngine = document.getElementById('tts-engine-select') ? document.getElementById('tts-engine-select').value : 'default';
            const selectedVoice = ttsEngine === 'default' 
                                    ? document.getElementById('voice-select').value 
                                    : document.getElementById('gemini-voice-select').value;
            
            // -----------------------------------------------------
            // NEW REAL-TIME API CHUNKING ENGINE (100% PERFECT SYNC)
            // -----------------------------------------------------
            
            const chunks = [];
            let lastCutIndex = 0;
            const narrationLower = text.toLowerCase();
            
            if (syncElements && syncElements.length > 0) {
                let activeElementIndex = 0;
                
                const sentences = text.match(/[^.!?]+[.!?]+/g) || [text];
                
                sentences.forEach((sen) => {
                    let senLower = sen.toLowerCase();
                    let elemsToReveal = [];
                    
                    while (activeElementIndex < syncElements.length) {
                        const elText = syncElements[activeElementIndex].textContent.trim().toLowerCase();
                        const cleanText = elText.replace(/[.,/#!$%^&*;:{}=\-_`~()]/g,"");
                        const words = cleanText.split(/\s+/).filter(w => w.length > 3).slice(0, 2);
                        
                        if (words.length > 0 && senLower.includes(words[0])) {
                            elemsToReveal.push(syncElements[activeElementIndex]);
                            activeElementIndex++;
                        } else {
                            break;
                        }
                    }
                    
                    if (sen.trim()) {
                        chunks.push({ text: sen.trim(), elementsToReveal: elemsToReveal });
                    }
                });
                
                while (activeElementIndex < syncElements.length) {
                    if (chunks.length > 0) {
                        chunks[chunks.length-1].elementsToReveal.push(syncElements[activeElementIndex]);
                    }
                    activeElementIndex++;
                }
            } else {
                chunks.push({ text: text, elementsToReveal: [] });
            }

            if (ttsEngine === 'default') {
                const utterance = new SpeechSynthesisUtterance(text);
                const voices = window.speechSynthesis.getVoices();
                const voiceObj = voices.find(v => v.name === selectedVoice);
                if (voiceObj) utterance.voice = voiceObj;
                utterance.rate = ttsState.rate;
                
                utterance.onstart = () => {
                    ttsState.isPlaying = true;
                    updateTTSButtons();
                    triggerFallbackReveal();
                };
                utterance.onend = () => {
                    if (ttsState.isPlaying) {
                        if (currentSlide < slides.length - 1) {
                            currentSlide++;
                            setTimeout(() => {
                                if (ttsState.isPlaying) renderSlide(currentSlide);
                            }, 800);
                        } else {
                            ttsState.isPlaying = false;
                            updateTTSButtons();
                        }
                    }
                };
                if (ttsState.isPlaying) window.speechSynthesis.speak(utterance);
                return;
            }

            async function fetchAudioForChunk(chunkText) {
                const requestBody = {
                    text: chunkText,
                    voice: selectedVoice,
                    tts_engine: ttsEngine
                };
                const res = await fetch('/generate-audio', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(requestBody)
                });
                if (!res.ok) throw new Error("Audio chunk generation failed");
                const data = await res.json();
                if (data.status !== 'success') throw new Error(data.detail);
                return data.audio_url;
            }

            ttsState.isPlaying = true;
            updateTTSButtons();

            try {
                let nextAudioPromise = fetchAudioForChunk(chunks[0].text);

                for (let i = 0; i < chunks.length; i++) {
                    if (window.currentNarrationId !== thisNarrationId) return;

                    const audioUrl = await nextAudioPromise;
                    
                    if (i + 1 < chunks.length) {
                        nextAudioPromise = fetchAudioForChunk(chunks[i + 1].text);
                    }

                    if (window.currentNarrationId !== thisNarrationId) return;

                    const audio = new Audio(audioUrl);
                    audio.crossOrigin = "anonymous";
                    audio.style.display = 'none';
                    document.body.appendChild(audio);
                    
                    window.currentAudio = audio;
                    audio.playbackRate = ttsState.rate;

                    if (chunks[i].elementsToReveal && chunks[i].elementsToReveal.length > 0) {
                        chunks[i].elementsToReveal.forEach(el => {
                            if (el) {
                                el.classList.remove('cascade-hidden');
                                el.classList.add('cascade-visible');
                            }
                        });
                    }

                    await new Promise((resolve, reject) => {
                        audio.addEventListener('ended', resolve);
                        audio.addEventListener('error', reject);
                        
                        const checkPlayState = setInterval(() => {
                            if (!ttsState.isPlaying && !audio.paused) {
                                audio.pause();
                            } else if (ttsState.isPlaying && audio.paused) {
                                audio.play().catch(e => console.log("Auto-play prevented", e));
                            }
                            if (window.currentNarrationId !== thisNarrationId) {
                                clearInterval(checkPlayState);
                                audio.pause();
                                resolve();
                            }
                        }, 100);

                        audio.addEventListener('ended', () => clearInterval(checkPlayState));
                        audio.addEventListener('error', () => clearInterval(checkPlayState));

                        if (ttsState.isPlaying) {
                            audio.play().catch(reject);
                        }
                    });
                    
                    if (audio.parentNode) {
                        audio.parentNode.removeChild(audio);
                    }
                }
                
                if (window.currentNarrationId !== thisNarrationId) return;
                
                if (ttsState.isPlaying) {
                    if (currentSlide < slides.length - 1) {
                        currentSlide++;
                        setTimeout(() => {
                            if (ttsState.isPlaying) renderSlide(currentSlide);
                        }, 800);
                    } else {
                        ttsState.isPlaying = false;
                        updateTTSButtons();
                    }
                }

            } catch (err) {
                console.error("Realtime Chunk API Sync Error:", err);
                triggerFallbackReveal();
                ttsState.isPlaying = false;
                updateTTSButtons();
            }
        }

        function updateTTSButtons() {
            if (!ttsPlayBtn || !ttsMuteBtn) return;

            if (ttsState.isPlaying) {
                ttsPlayBtn.innerHTML = '<span class="btn-icon">⏸</span> Pause';
                ttsPlayBtn.classList.add('active');
            } else {
                ttsPlayBtn.innerHTML = '<span class="btn-icon">▶</span> Play';
                ttsPlayBtn.classList.remove('active');
            }

            if (ttsState.isMuted) {
                ttsMuteBtn.innerHTML = '<span class="btn-icon">🔇</span> Unmute';
                ttsMuteBtn.classList.add('active');
            } else {
                ttsMuteBtn.innerHTML = '<span class="btn-icon">🔊</span> Mute';
                ttsMuteBtn.classList.remove('active');
            }
        }

        // Button Event Bindings
        ttsPlayBtn.addEventListener('click', () => {
            if (ttsState.isPlaying) {
                if (window.currentAudio) window.currentAudio.pause();
                ttsState.isPlaying = false;
                updateTTSButtons();
            } else {
                ttsState.isPlaying = true;
                if (window.currentAudio && window.currentAudio.paused) {
                    window.currentAudio.play();
                } else {
                    const slide = slides[currentSlide];
                    if (slide && slide.narration) {
                        speakNarration(slide.narration);
                    }
                }
                updateTTSButtons();
            }
        });

        ttsStopBtn.addEventListener('click', () => {
            if (window.currentAudio) window.currentAudio.pause();
            ttsState.isPlaying = false;
            updateTTSButtons();
        });

        ttsMuteBtn.addEventListener('click', () => {
            ttsState.isMuted = !ttsState.isMuted;
            if (ttsState.isMuted) {
                if (window.currentAudio) window.currentAudio.pause();
                ttsState.isPlaying = false;
            } else {
                const slide = slides[currentSlide];
                if (slide && slide.narration) {
                    speakNarration(slide.narration);
                }
            }
            updateTTSButtons();
        });

        ttsRateSlider.addEventListener('input', (e) => {
            const newRate = parseFloat(e.target.value);
            ttsState.rate = newRate;
            ttsRateValue.textContent = `${newRate.toFixed(1)}x`;
            
            if (window.currentAudio) {
                window.currentAudio.playbackRate = newRate;
            }
        });

        // Start Server Button Action
        const startServerBtn = document.getElementById('start-server-btn');

        // TTS Mode Toggle Logic
        const ttsEngineSelect = document.getElementById('tts-engine-select');
        const voiceSelect = document.getElementById('voice-select');
        const geminiVoiceSelect = document.getElementById('gemini-voice-select');

        if (ttsEngineSelect) {
            ttsEngineSelect.addEventListener('change', () => {
                if (ttsEngineSelect.value === 'default') {
                    voiceSelect.disabled = false;
                    geminiVoiceSelect.disabled = true;
                    geminiVoiceSelect.style.opacity = '0.5';
                    voiceSelect.style.opacity = '1';
                } else {
                    voiceSelect.disabled = true;
                    geminiVoiceSelect.disabled = false;
                    geminiVoiceSelect.style.opacity = '1';
                    voiceSelect.style.opacity = '0.5';
                }
            });
        }
        const hardwareSelect = document.getElementById('hardware-select');
        
        startServerBtn.addEventListener('click', async () => {
            const profile = hardwareSelect.value;
            
            // UI Loading state
            startServerBtn.disabled = true;
            const originalText = startServerBtn.innerHTML;
            startServerBtn.innerHTML = '<span class="spinner" style="width:16px; height:16px; border-width:2px; margin:0;"></span> Loading...';
            startServerBtn.style.opacity = '0.7';
            
            try {
                const response = await fetch('/start-ai-server', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ profile: profile })
                });
                
                if (!response.ok) {
                    const errorData = await response.json();
                    throw new Error(errorData.detail || 'Failed to start AI server');
                }
                
                const data = await response.json();
                
                // UI Success state
                startServerBtn.innerHTML = '✔ Server Online';
                startServerBtn.style.background = '#28a745';
                startServerBtn.style.borderColor = '#28a745';
                startServerBtn.style.color = 'white';
                startServerBtn.style.opacity = '1';
                // Keep it disabled so they don't click it again unnecessarily
                
                // Allow generating now that server is confirmed online
                generateBtn.disabled = !selectedFile;
                
            } catch (err) {
                console.error(err);
                alert('Error starting AI server: ' + err.message);
                
                // Reset UI
                startServerBtn.disabled = false;
                startServerBtn.innerHTML = originalText;
                startServerBtn.style.opacity = '1';
            }
        });

        // Generate Slides Button Action
        generateBtn.addEventListener('click', async () => {
            const modelName = modelSelect.value.trim() || 'gemini-2.5-flash';
            if (!selectedFile) {
                alert('Please choose or drop a PDF or DOCX file first.');
                return;
            }



            // Show loading overlay
            loadingOverlay.classList.remove('hidden');
            loadingOverlay.classList.add('active');
            loadingLog.textContent = '';
            logStatus('Reading File...', `Loading file: ${selectedFile.name}`);

            try {
                // Read file as array buffer
                const arrayBuffer = await new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = () => resolve(reader.result);
                    reader.onerror = (err) => reject(err);
                    reader.readAsArrayBuffer(selectedFile);
                });

                let extractedText = '';
                
                if (selectedFile.name.toLowerCase().endsWith('.pdf')) {
                    logStatus('Extracting Text...', 'Starting PDF.js parser...');
                    const typedarray = new Uint8Array(arrayBuffer);
                    const pdf = await pdfjsLib.getDocument(typedarray).promise;
                    const numPages = pdf.numPages;
                    logStatus('Extracting Text...', `PDF loaded successfully. Total pages: ${numPages}`);

                    for (let i = 1; i <= numPages; i++) {
                        logStatus('Extracting Text...', `Reading page ${i} of ${numPages}...`);
                        const page = await pdf.getPage(i);
                        const textContent = await page.getTextContent();
                        const pageText = textContent.items.map(item => item.str).join(' ');
                        extractedText += `--- Page ${i} ---\n${pageText}\n\n`;
                    }
                } else if (selectedFile.name.toLowerCase().endsWith('.docx') || selectedFile.type.includes('wordprocessingml')) {
                    logStatus('Extracting Text...', 'Starting Mammoth.js DOCX parser...');
                    const result = await mammoth.extractRawText({arrayBuffer: arrayBuffer});
                    extractedText = result.value;
                    logStatus('Extracting Text...', `DOCX loaded successfully.`);
                }

                // Cap the input to roughly 65,536 tokens (~262,144 characters) to save costs
                const MAX_CHARS = 262144;
                if (extractedText.length > MAX_CHARS) {
                    logStatus('Extracting Text...', `Warning: Document is too large. Truncating from ${extractedText.length} characters to ${MAX_CHARS} characters (~65,536 tokens)...`);
                    extractedText = extractedText.substring(0, MAX_CHARS);
                }

                logStatus('Sending to Gemini...', `Extracted ${extractedText.length} characters of text. Communicating with Gemini API...`);

                const systemPrompt = `You are an expert documentary director and educational content creator. Your task is to analyze the extracted text from a PDF.

  CRITICAL RULE - PRESERVE ALL CONTENT:
  You MUST preserve ALL information, points, sections, and details from the source document. Do NOT summarize, condense, truncate, or skip any content. Every single concept or paragraph from the source must be represented in the final presentation. It is absolutely FORBIDDEN to summarize the document for the sake of brevity.

  IMPORTANT SCRIPT PARSING RULES:
  The uploaded PDF is likely a PRE-WRITTEN SCRIPT containing explicit directions like "Board content", "Aadhi speaks (voiceover)", and "Animation". 
  You MUST parse this script faithfully:
  - Map the "Board content" directly into the "html" field.
  - Map the "Aadhi speaks" or voiceover text EXACTLY word-for-word into the "narration" field. Do NOT invent your own voiceover if one is provided! You may ONLY regenerate or modify the specific voiceover script if the provided content is way too long and ineffective for a single scene.
  - Map "Animation" directions into "manim_code" simulations or "ai_video" prompts depending on what fits best.
  - If the PDF is NOT a script but a normal textbook, you may generate your own highly engaging voiceover and visuals as usual.

CRITICAL IMAGE PLACEHOLDER RULE:
If the source document explicitly references a diagram, figure, or image (e.g., "Image 1: Static motor" or "Figure 3"), you MUST place an image placeholder exactly where it belongs in the 'html' content using this exact format:
<div class='board-image-placeholder' data-img-id='Image_1_Static_motor'>[Image Placeholder: Image_1_Static_motor]</div>
Do not attempt to describe or recreate the image data yourself if it's too complex. The system will detect this placeholder and provide the user an option to upload the actual image file directly onto the main board. Do NOT put these placeholders in the 'side_panel' object; they must go in the 'html' of a 'content' scene.

LEFT PANE HTML ENRICHMENT RULES:
You must make the 'html' content engaging by injecting the following elements alongside text:
1. DYNAMIC GIFs: To visually demonstrate an action/concept inline, output: <img class="context-gif" data-query="short search term for gif">. (e.g. data-query="spinning engine"). The system will auto-fetch and play it!
2. GLOWING CALLOUTS: Break up walls of text by putting important concepts in callouts:
   - <div class="info-callout">Fascinating fact or definition</div>
   - <div class="tip-callout">Pro-tips, shortcuts, or best practices</div>
   - <div class="warning-callout">Common mistakes, safety warnings, or strict rules</div>
3. CODE BLOCKS: If explaining programming, use: <pre><code class="language-python">print("Hello")</code></pre>. The system will auto-highlight it.
4. HTML TABLES: If the source document contains tabular data, comparisons, or structured data, you MUST strictly output it as a proper HTML '<table>'. Do NOT convert table data into pointwise text or bullet points!

CRITICAL SLIDE CHUNKING RULE (DO NOT IGNORE):
The presentation board is EXTREMELY small. A slide can show only a very limited amount of content! You MUST adhere to these STRICT limits for the 'html' field of a "content" scene:
- MAXIMUM 3 bullet points per slide.
- MAXIMUM 40 words total per slide.
If your content exceeds these limits, you MUST split it into multiple consecutive "content" scenes. Add "(Contd.)" to the title of the subsequent scenes. DO NOT try to cram 5 or 6 bullet points into one scene. If you go beyond these limits, the text will overflow beyond the board's frame, break the UI, and be completely unreadable to the user! Split, split, split!

The output works like an immersive cinematic video. There are no "slides", only continuous "scenes".
CRITICAL JSON STRUCTURE: You must return a JSON OBJECT containing exactly three keys: "subject_name", "concept_map" and "scenes".
- "subject_name": The exact name of the subject/course extracted from the PDF. CRITICAL: You MUST explicitly REMOVE any subject code, course code, or alphanumeric IDs from this name (e.g., change "CS101 Intro to Physics" to just "Intro to Physics").
- "concept_map": A JSON array of nodes representing the Skill Tree. CRITICAL REQUIREMENT: You MUST ALWAYS deduce and generate a detailed concept map based on the progression of topics in the document, EVEN IF the source document is a pre-written script that doesn't explicitly have a skill tree section. The frontend application WILL CRASH if this array is empty or missing. You MUST extract at least 3-10 logical concepts. Each node has: {"id": "unique_string", "title": "Short Title", "depends_on": ["parent_id" or empty array]}.
- "scenes": An array of scene objects. Every scene MUST include a "concept_id" that strictly matches one of the IDs in your concept_map. Every scene MUST also include a "side_panel" object. The "side_panel" object must be ONE of these EIGHT formats:
  1) {"type": "skill_tree"} -> Shows the concept map. Use this for the title slide or major topic transitions.
  2) {"type": "image", "prompt": "hyper-realistic stock photo of [subject], photography, 8k resolution, highly detailed"} -> Shows a contextual AI-generated image. Use this frequently for content slides to keep the presentation visually engaging.
  3) {"type": "chart", "chart_type": "bar", "title": "Chart Title", "data": { "labels": ["A", "B"], "datasets": [{"label": "Value", "data": [10, 20], "backgroundColor": "rgba(176,38,255,0.5)", "borderColor": "rgba(255,215,0,1)"}] }} -> Renders a gorgeous animated Chart.js data visualization. Use this whenever the text discusses quantitative data, history timelines, or comparisons! Supported chart_types: bar, line, pie, doughnut.
  4) {"type": "3d_model", "model_name": "atom"} -> Renders an interactive 3D model that the user can drag. Supported model_names: atom, torus, box.
  5) {"type": "quiz", "question": "What is X?", "options": ["A", "B", "C"], "correct_index": 0} -> Generates an interactive multiple-choice knowledge check! Use this to quiz the user on what was just taught.
  6) {"type": "terminal", "command": "./run_sim.sh", "output": "Simulation complete. Accuracy: 99.9%"} -> Renders a live hacker terminal typing out the output. Great for coding, tech, or logic explanations!
  7) {"type": "graph", "title": "Quadratic Curve", "function": "x*x + 2*x"} -> Renders a live, interactive JSXGraph Cartesian coordinate system plotting the math function. Use this for ANY math, physics, or geometry topic!
  8) {"type": "manim", "title": "Visual Proof", "manim_code": "..."} -> SMALL Python Manim animation for the side panel. ONLY use for simple, small animations! Scale all objects down heavily (e.g., .scale(0.4)) so they physically fit within the narrow tight width of the side panel! Use 'VGroup' and '.arrange(DOWN)' to ensure elements strictly align without overlapping! Do NOT use this for complex simulations.

  ADVANCED MANIM ANIMATION RULES:
  If you generate Manim code, you MUST adhere to these cinematic rules:
  - PREFER 'MovingCameraScene': If the scene has multiple elements, use 'MovingCameraScene' and animate 'self.camera.frame.animate.move_to(obj)' to dynamically pan/zoom.
  - USE MORPHING: For mathematical formulas, strictly use 'TransformMatchingTex' instead of 'Transform' or 'Write' to visually morph equations seamlessly.
  - HIGHLIGHTING: To emphasize concepts during the narration, use 'SurroundingRectangle(element, color=YELLOW, buff=0.1)' and animate its creation.
  - OVERLAP PREVENTION: You MUST use 'VGroup(*elements).arrange(DOWN, aligned_edge=LEFT)' or explicitly set coordinates to completely prevent overlaps.

  CRITICAL LAYOUT RULE: NEVER use "ai_video" or full-scale Manim simulations inside the "side_panel" object! AI videos and complex Manim simulations MUST be generated as entirely separate FULL SCREEN scene types (see scene types below).

Here are the types of scenes you can generate:

1. Title Scene:
   {"type": "title", "concept_id": "intro", "title": "Session [Number] - [Topic Name]", "subtitle": "Subtitle or Topic Info", "narration": "Highly conversational, passionate, and natural human narration. Sound like a documentary host, not a textbook."}
   *CRITICAL TITLE RULE*: If the source document introduces a Session, Chapter, or major topic, you MUST create a "title" scene for it. NEVER put a Session title on a "content" scene (the board). The title MUST be strictly formatted as 'Session [Number] - [Topic Name]'. Do NOT output bland titles like '2 - Stress and Strain'. It must be 'Session 2 - Stress and Strain'.

2. Content Scene (Immersive Text Overlay):
   {"type": "content", "concept_id": "concept1", "title": "CONCEPT", "subtitle": "Optional Subtitle", "html": "...", "narration": "Conversational, natural spoken explanation of the concept."}
   *CRITICAL RULE*: Keep the 'html' content visually concise enough to fit on one screen without scrolling. You MUST write EVERY SINGLE SENTENCE as a separate <p> tag, <li> tag, or <div class='definition'> tag. Do NOT group multiple sentences into one paragraph! This ensures each sentence animates onto the screen one by one. All heavy conceptual explanations MUST go into the spoken 'narration'.

3. Manim Simulation (Math/Logic Visualization):
   {"type": "simulation", "concept_id": "concept1", "title": "VISUALIZING X", "visual_reasoning": "...", "manim_code": "...", "narration": "Enthusiastic spoken narration explaining the visual logic."}

4. Example Scene (for solved problems, derivations, examples):
   {"type": "example", "title": "EXAMPLE TITLE", "subtitle": "Optional Subtitle", "html": "HTML content", "narration": "Conversational, step-by-step guidance."}

5. Key Takeaway Scene (uses takeaway-list class):
   {"type": "key-takeaway", "title": "TAKEAWAY TITLE", "html": "<ul class='takeaway-list'><li>Takeaway 1</li><li>Takeaway 2</li></ul>", "narration": "Detailed spoken narration text summarizing takeaways"}

6. Summary Scene (uses styled tables for comparison/quick reference):
   {"type": "summary", "title": "SUMMARY TITLE", "html": "<table><thead><tr><th>Header 1</th><th>Header 2</th></tr></thead><tbody><tr><td>Data 1</td><td>Data 2</td></tr></tbody></table>", "narration": "Detailed spoken narration text summarizing the comparison or data"}

7. AI Cinematic Video Scene (Cinematic B-roll via Text-to-Video AI):
   {"type": "ai_video", "concept_id": "concept1", "title": "CINEMATIC TITLE", "subtitle": "Optional Subtitle", "prompt": "Highly descriptive, mechanically accurate, 30-word max prompt of the exact physical shot you want (e.g., 'A slow-motion tracking shot of a heavy steel suspension bridge cable pulling taut under extreme tension, shot on 35mm film, highly detailed, photorealistic, 4k.')", "narration": "Detailed spoken narration text that plays while the video loops"}

8. Interactive Web Simulation (p5.js):
   {"type": "p5_simulation", "concept_id": "concept1", "title": "INTERACTIVE CANVAS", "p5_code": "function setup() { createCanvas(800, 600); background(0); }\\nfunction draw() { fill(255, 215, 0); ellipse(mouseX, mouseY, 50, 50); }", "narration": "Explain the interactive elements to the user. E.g., 'Try dragging your mouse across the screen to see how gravity affects the orbit!'"}

Styling guidelines for content within the 'html' field:
- Definitions: Wrap definitions or core concepts in: <div class='definition'><strong>Definition:</strong> Detailed description.</div>
- Keywords: Wrap key terms in <span class="keyword">key term</span> to apply the glowing effect.
- Math/Equations: Wrap equations in MathJax format. Block equations must use \\\\[ ... \\\\] (double escaped backslash). Inline equations must use \\\\( ... \\\\) (double escaped backslash). For example: \\\\sigma = \\\\frac{F}{A}.
- Lists: Use <ul> and <li> elements. Avoid plain text paragraphs; prefer structured lists.
- Section Headings: Inside content scenes, use <h2 class='section-heading'>Subheading</h2> to organize content.
- Code snippets: Wrap inline code in <span class="inline-code">code</span>.

AI Cinematic Video guidelines (For "ai_video" type scenes ONLY):
- CRITICAL: The 'prompt' MUST be structured explicitly for the LTX-Video AI model.
- LTX-Video requires highly descriptive, physical prompts. Describe the subject, their exact motion, the background setting, the lighting, and the camera movement (e.g., 'Panning shot', 'Zoom in').
- Structure: [Camera movement] of [Subject] doing [Action] in [Setting], [Lighting/Atmosphere].
- Example: "A slow-motion tracking shot of a heavy steel suspension bridge cable pulling taut under extreme tension, shot on 35mm film, highly detailed, photorealistic, 4k."
- CRITICAL: AVOID using fancy, sci-fi, or cliché words like "futuristic", "glowing", "neon", or "cyberpunk" unless the text explicitly demands it. Write highly grounded, photorealistic, and mechanically detailed descriptions of real-world objects and their exact materials (e.g. steel, concrete, water).
- Do NOT describe abstract concepts, text, or math equations in the prompt. Describe ONLY concrete physical objects, their exact materials, and their physical movement!
- Make it sound like a highly detailed, professional B-roll footage description for a high-end documentary.

Simulation Script guidelines (For "simulation" type scenes ONLY):
- CHAIN OF THOUGHT FOR MEANINGFUL VISUALS: You MUST include a 'visual_reasoning' field before the 'manim_code'. In it, explain how your animation directly maps to the specific facts, equations, or diagrams in the uploaded PDF text.
- CRITICAL: DO NOT draw random, generic abstract shapes. Your animations MUST be meaningful. If the text is about engineering, draw the physical object! If it's about network protocols, draw packets moving! Visualize the actual content provided.
- CRITICAL HALLUCINATION RULES: 
  1. Do NOT use SVGMobject or ImageMobject under any circumstances, because there are no external image files available. You MUST construct objects using basic shapes like Circle, Square, Rectangle, and Line.
  2. Do NOT use \`Tex\` for math equations or variables containing underscores (e.g. Y_{11}). You MUST use \`MathTex\` for all mathematical formulas, variables, and equations.
  3. Do NOT use \`.reverse_path()\`. To reverse a line or path in Manim, use \`.reverse_direction()\`.
- You MUST write raw Python code using the 'manim' library in the 'manim_code' field. DO NOT write javascript.
- The code MUST contain exactly ONE class that inherits from 'Scene' (e.g., class MyScene(Scene):).
- Keep animations under 15 seconds. Use 'self.play()' to animate shapes, text, and equations intuitively.
- IMPORTANT: The python code string MUST be properly JSON escaped. Use \\n for newlines and escape quotes (\\"). Do not use backticks or markdown wrappers.
- MANIM CODE PITFALLS:
  1. DO NOT use '.get_top_left()', '.get_top_right()', '.get_bottom_left()', or '.get_bottom_right()'. Mobjects do not have these attributes. Instead, use '.get_corner(UL)' (upper left), '.get_corner(UR)' (upper right), '.get_corner(DL)' (down left), or '.get_corner(DR)' (down right).
  2. You can use '.get_top()', '.get_bottom()', '.get_left()', and '.get_right()' for simple edges.
  3. 'Rectangle' does NOT accept 'corner_radius'. If you want rounded corners, you MUST use 'RoundedRectangle(corner_radius=...)' instead.
  4. OVERLAPPING PREVENTION (CRITICAL): Elements MUST NEVER overlap! Always explicitly group elements using 'VGroup' and align them perfectly using '.arrange(DOWN, center=False, aligned_edge=LEFT)' or explicitly set coordinates so they are mathematically separated.
  5. SCALING: Keep text and formulas scaled appropriately (e.g., .scale(0.8)) so they do not bleed off the edges of the screen or overlap with other large shapes.
  6. LATEX WARNING: ALWAYS use raw strings for MathTex and Tex (e.g., MathTex(r"\frac{a}{b}")). If you don't use raw strings, the backslashes will break (e.g., \f will become a form feed).
  7. CONTENT ACCURACY (CRITICAL): Ensure that you accurately represent all mathematical equations, parameters, and diagrams from the source document. Do NOT skip variables or misrepresent formulas. Be exhaustively accurate.

Pacing Strategy:
- CRITICAL REQUIREMENT: You MUST generate 'ai_video' scenes frequently! Do NOT rely solely on 'simulation' (Manim) scenes. It is an absolute necessity that you include AI Cinematic Videos throughout the presentation.
- Alternate between Content scenes, Manim Simulations, and AI Cinematic Video scenes.
- Start a new topic with a Content scene to define terms and core formulas. Keep text concise enough to not scroll.
- If the topic involves heavy math or diagrams, follow up with a Manim Simulation.
- Every major concept MUST feature an AI Cinematic Video scene ('ai_video') to show a real-world, highly visual application of the concept.
- FINAL SLIDE RULE: The very last scene of the presentation MUST be a 'content' scene featuring a concluding summary. To allow the Mascot to return to the screen for the final sign-off, you MUST place an 'ai_video' scene immediately before this final scene.

Formatting and Narration constraints:
- Do NOT generate markdown image tags in the html field. Use the 'manim_code' field exclusively for video simulations.
- Ensure all MathJax equations are mathematically correct and properly escaped.
- TERMINOLOGY RULE: Do NOT use the words "Video 1", "Video 2", or refer to the content as a "video". You MUST always refer to it as a "Session" (e.g., "Welcome to Session 1", "In this session").
- CRITICAL NARRATION RULE: Every single scene object (including 'simulation' and 'ai_video' types) MUST contain a 'narration' field! It is strictly FORBIDDEN to omit the 'narration' field. 
- The narration across the entire presentation must flow like a single, cohesive documentary script with perfect content consistency. 
- Each narration should be a rich, passionate, and highly detailed spoken explanation (at least 4-8 sentences) that deeply explores the concepts. Do not use mathematical formulas directly in the narration text; write them out in plain spoken English (e.g., "sigma equals force over area").
- For 'simulation' (Manim) scenes, the narration MUST explain exactly what the student is seeing happen on screen in real-time.
- Return ONLY a valid JSON object with the 'subject_name', 'concept_map' and 'scenes' keys. Do not write any explanations, markdown code blocks, or conversational text. Start with '{' and end with '}'.`;

                const response = await fetch('/generate-script', {
                    method: 'POST',
                    headers: {
                        'content-type': 'application/json'
                    },
                    body: JSON.stringify({
                        prompt_text: `${systemPrompt}\n\nHere is the extracted text from the PDF:\n${extractedText}`,
                        model_name: modelName
                    })
                });

                if (!response.ok) {
                    const errorText = await response.text();
                    throw new Error(`API returned ${response.status}: ${errorText}`);
                }

                logStatus('Processing Response...', 'Reading streaming response from backend...');
                
                const reader = response.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buffer = '';
                let finalJsonData = null;

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    
                    buffer += decoder.decode(value, { stream: true });
                    let lines = buffer.split('\n');
                    buffer = lines.pop(); // keep partial line in buffer
                    
                    for (let line of lines) {
                        line = line.trim();
                        if (!line) continue;
                        
                        if (line.startsWith('PROGRESS:')) {
                            const chars = line.split(':')[1];
                            logStatus('Generating Presentation...', `Received ${chars} characters from AI...`);
                        } else if (line.startsWith('FINAL_JSON:')) {
                            const jsonStr = line.substring('FINAL_JSON:'.length);
                            finalJsonData = JSON.parse(jsonStr);
                        } else if (line.startsWith('ERROR:')) {
                            throw new Error(line.substring('ERROR:'.length));
                        }
                    }
                }
                
                if (buffer.trim().startsWith('FINAL_JSON:')) {
                    const jsonStr = buffer.trim().substring('FINAL_JSON:'.length);
                    finalJsonData = JSON.parse(jsonStr);
                } else if (buffer.trim().startsWith('ERROR:')) {
                    throw new Error(buffer.trim().substring('ERROR:'.length));
                }
                
                if (!finalJsonData) {
                    throw new Error("Did not receive final JSON data from backend.");
                }
                
                let parsedData = finalJsonData;
                
                let newSlides = [];
                if (Array.isArray(parsedData)) {
                    // Backward compatibility with old Array format
                    newSlides = parsedData;
                    conceptMapData = null;
                } else if (parsedData && Array.isArray(parsedData.scenes)) {
                    // New Format with Concept Map
                    newSlides = parsedData.scenes;
                    conceptMapData = parsedData.concept_map || null;
                    currentSubjectName = parsedData.subject_name || "Subject Overview";
                }

                if (!Array.isArray(newSlides) || newSlides.length === 0) {
                    throw new Error('Generated output is not a valid non-empty array of scenes.');
                }

                logStatus('Rendering Video...', `Successfully parsed ${newSlides.length} scenes. Transitioning UI...`);

                // Update slides array in place
                slides.length = 0;
                slides.push(...newSlides);

                // Auto-backup to localStorage so they survive page refreshes
                saveProjectBackup();

                // Reset slide index
                currentSlide = 0;

                // Transition screen view to Start Overlay
                showStartOverlay();

            } catch (err) {
                console.error(err);
                logStatus('Error Occurred', `Failed: ${err.message}`);
                
                // Add retry/dismiss action button
                const errDiv = document.createElement('div');
                errDiv.className = 'error-message';
                errDiv.innerHTML = `
                    <strong>Generation Failed</strong>
                    <span>${err.message}</span>
                    <button class="retry-btn" type="button">Dismiss</button>
                `;
                errDiv.querySelector('.retry-btn').addEventListener('click', () => {
                    errDiv.remove();
                });
                loadingOverlay.appendChild(errDiv);
            } finally {
                // Reset file inputs if needed, but do not hide loading overlay automatically on success since it transitions away
                const errDiv = loadingOverlay.querySelector('.error-message');
                if (!errDiv) {
                    loadingOverlay.classList.remove('active');
                    setTimeout(() => loadingOverlay.classList.add('hidden'), 400); // Wait for fade out
                }
            }
        });

        // Play Demo button handler
        document.getElementById('demo-btn').addEventListener('click', () => {
            showStartOverlay();
        });

        // Initialize first slide when MathJax is ready or document is loaded
        document.addEventListener("DOMContentLoaded", () => {
            // Dynamically load background video to permanently bypass browser cache
            const mascotBg = document.getElementById('mascot-bg');
            mascotBg.innerHTML = `
                <source src="video_template/video_template.mp4?t=${Date.now()}" type="video/mp4">
                <source src="video_template/provided-video.mp4?t=${Date.now()}" type="video/mp4">
            `;
            mascotBg.load();

            const savedSlides = localStorage.getItem('saved_slides_backup');
            if (savedSlides) {
                try {
                    const parsed = JSON.parse(savedSlides);
                    let restoredSlides = [];
                    
                    if (Array.isArray(parsed) && parsed.length > 0) {
                        // Backward compatibility (old backup)
                        restoredSlides = parsed;
                    } else if (parsed && parsed.slides && Array.isArray(parsed.slides) && parsed.slides.length > 0) {
                        // New format
                        restoredSlides = parsed.slides;
                        if (parsed.conceptMapData) {
                            conceptMapData = parsed.conceptMapData;
                        }
                        if (parsed.subjectName) {
                            currentSubjectName = parsed.subjectName;
                        }
                    }

                    if (restoredSlides.length > 0) {
                        slides.length = 0;
                        slides.push(...restoredSlides);
                        
                        const restoreBtn = document.getElementById('restore-btn');
                        if (restoreBtn) {
                            restoreBtn.style.display = 'block';
                            restoreBtn.addEventListener('click', () => {
                                currentSlide = 0;
                                document.getElementById('setup-screen').classList.add('hidden');
                                showStartOverlay();
                            });
                        }
                    }
                } catch(e) {
                    console.error("Failed to restore slides", e);
                }
            }
        });

        // --- Video Recorder Feature ---
        let mediaRecorder;
        let recordedChunks = [];
        
        document.getElementById('record-btn').addEventListener('click', async () => {
            const btn = document.getElementById('record-btn');
            
            if (mediaRecorder && mediaRecorder.state === 'recording') {
                // Stop recording
                mediaRecorder.stop();
                btn.innerHTML = '<span class="btn-icon">⏺</span> Record Video';
                btn.style.background = '#ff4d4d';
                return;
            }

            try {
                // Ask user to select the tab to record (with audio)
                const stream = await navigator.mediaDevices.getDisplayMedia({
                    video: { displaySurface: "browser" },
                    audio: true,
                    selfBrowserSurface: "include",
                    preferCurrentTab: true,
                    systemAudio: "include"
                });

                mediaRecorder = new MediaRecorder(stream, { mimeType: 'video/webm' });
                recordedChunks = [];

                mediaRecorder.ondataavailable = function(e) {
                    if (e.data.size > 0) recordedChunks.push(e.data);
                };

                mediaRecorder.onstop = function() {
                    const blob = new Blob(recordedChunks, { type: 'video/webm' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    document.body.appendChild(a);
                    a.style = 'display: none';
                    a.href = url;
                    a.download = 'presentation_export.webm';
                    a.click();
                    window.URL.revokeObjectURL(url);
                    
                    // Stop all tracks to end sharing
                    stream.getTracks().forEach(track => track.stop());
                };

                mediaRecorder.start();
                btn.innerHTML = '<span class="btn-icon">⏹</span> Stop Recording';
                btn.style.background = '#888';
                
            } catch(err) {
                console.error("Recording error:", err);
                alert("Failed to start recording. Please make sure you grant screen recording permissions.");
            }
        });

        // --- Ultimate Auto Export Feature ---
        let isAutoExporting = false;
        let autoExportRecorder = null;
        let autoExportChunks = [];

        document.getElementById('auto-export-btn').addEventListener('click', async () => {
            if (isAutoExporting) return; // Prevent double click
            
            // Check if we have a presentation ready
            if (!slides || slides.length === 0) {
                alert("Generate a video first before exporting!");
                return;
            }

            try {
                alert("CRITICAL: When the share popup appears, you MUST select the 'Share this tab' option and check 'Also share tab audio' at the bottom. If you do not share audio, the recording will fail!");
                // Ask user to select the tab to record
                const stream = await navigator.mediaDevices.getDisplayMedia({
                    video: { displaySurface: "browser" },
                    audio: true,
                    selfBrowserSurface: "include",
                    preferCurrentTab: true,
                    systemAudio: "include"
                });

                if (stream.getAudioTracks().length === 0) {
                    alert("ERROR: You forgot to check 'Also share tab audio' in the popup! The export has been cancelled. Please click Ultimate Export again and make sure to check the audio box.");
                    stream.getTracks().forEach(track => track.stop());
                    isAutoExporting = false;
                    return;
                }

                autoExportRecorder = new MediaRecorder(stream, { mimeType: 'video/webm' });
                autoExportChunks = [];

                autoExportRecorder.ondataavailable = function(e) {
                    if (e.data.size > 0) autoExportChunks.push(e.data);
                };

                autoExportRecorder.onstop = function() {
                    const blob = new Blob(autoExportChunks, { type: 'video/webm' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    document.body.appendChild(a);
                    a.style = 'display: none';
                    a.href = url;
                    a.download = 'ultimate_presentation_export.webm';
                    a.click();
                    window.URL.revokeObjectURL(url);
                    
                    stream.getTracks().forEach(track => track.stop());
                    
                    isAutoExporting = false;
                    document.getElementById('voice-control-bar').classList.remove('hidden-force');
                };

                isAutoExporting = true;
                // Immediately hide UI
                document.getElementById('voice-control-bar').classList.add('hidden-force');
                
                autoExportRecorder.start();
                
                // Buffer to let UI fade out before recording visually captures it
                setTimeout(() => {
                    startPresentationSequence(true);
                }, 500);

            } catch(err) {
                console.error("Auto Export error:", err);
                alert("Failed to start auto-export. Please grant screen & audio recording permissions.");
                isAutoExporting = false;
                document.getElementById('voice-control-bar').classList.remove('hidden-force');
            }
        });

        // --- Export HTML Presentation Feature ---
        document.getElementById('export-html-btn').addEventListener('click', () => {
            if (!slides || slides.length === 0 || slides.length === 5) {
                // If length is 5, it might be the hardcoded demo slides, but let's just make sure they generated something
                if (currentSubjectName === "Subject Overview" && slides[0].title === "WHAT IS STRESS?") {
                    alert("Please generate a new video first before exporting as HTML!");
                    return;
                }
            }

            // Clone the entire document
            const htmlClone = document.documentElement.cloneNode(true);

            // Remove the configuration input panel
            const configPanel = htmlClone.querySelector('.input-container');
            if (configPanel) configPanel.remove();

            // Remove script editor modal
            const scriptModal = htmlClone.querySelector('#script-editor-modal');
            if (scriptModal) scriptModal.remove();
            
            // Remove the Export HTML button from the cloned copy so they don't export an export
            const exportBtn = htmlClone.querySelector('#export-html-btn');
            if (exportBtn) exportBtn.remove();
            
            // Remove the auto export button from the cloned copy
            const ultimateBtn = htmlClone.querySelector('#auto-export-btn');
            if (ultimateBtn) ultimateBtn.remove();

            // Inject the state into the head
            const stateScript = document.createElement('script');
            stateScript.textContent = `
                window.__EXPORTED_STATE__ = {
                    slides: ${JSON.stringify(slides)},
                    conceptMapData: ${JSON.stringify(conceptMapData)},
                    currentSubjectName: ${JSON.stringify(currentSubjectName)},
                    selectedVoice: "${document.getElementById('voice-select').value}"
                };
            `;
            htmlClone.querySelector('head').appendChild(stateScript);

            // Get the final string
            const fullHtml = "<!DOCTYPE html>\\n" + htmlClone.outerHTML;
            const blob = new Blob([fullHtml], { type: "text/html;charset=utf-8" });
            const url = URL.createObjectURL(blob);
            
            // Trigger download
            const a = document.createElement('a');
            a.href = url;
            a.download = currentSubjectName.replace(/[^a-z0-9]/gi, '_').toLowerCase() + "_presentation.html";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
        });

        // --- Reload Slide Feature ---
        document.getElementById('reload-slide-btn').addEventListener('click', () => {
            if (window.currentAudio) window.currentAudio.pause();
            renderSlide(currentSlide);
        });

        // --- Script Editor Logic ---
        const editScriptBtn = document.getElementById('edit-script-btn');
        const scriptEditorModal = document.getElementById('script-editor-modal');
        const scriptEditorTextarea = document.getElementById('script-editor-textarea');
        const scriptEditorCancel = document.getElementById('script-editor-cancel');
        const scriptEditorSave = document.getElementById('script-editor-save');
        const editorTabs = document.querySelectorAll('.editor-tab');
        const scriptEditorHint = document.getElementById('script-editor-hint');

        let editorMode = 'slide'; // 'slide', 'narration', 'full'

        function populateEditor() {
            if (!slides || slides.length === 0) return;
            const sceneData = slides[currentSlide];
            const currentParsedJson = slides;
            const currentMode = editorMode;

            if (currentMode === 'slide') {
                scriptEditorTextarea.value = JSON.stringify(sceneData, null, 4);
                scriptEditorHint.innerText = "Edit the JSON object for the current scene.";
                document.getElementById('script-editor-save').style.display = 'inline-block';
                
                const hasManim = sceneData.type === 'simulation' || (sceneData.side_panel && sceneData.side_panel.type === 'manim');
                document.getElementById('script-editor-regen-manim').style.display = hasManim ? 'inline-block' : 'none';
                document.getElementById('script-editor-regen-feedback').style.display = hasManim ? 'block' : 'none';
                if (hasManim) document.getElementById('script-editor-regen-feedback').value = '';
                
            } else if (currentMode === 'narration') {
                scriptEditorTextarea.value = sceneData.narration || "";
                scriptEditorHint.innerText = "Edit the voiceover text for the current scene.";
                document.getElementById('script-editor-regen-feedback').style.display = 'none';
                document.getElementById('script-editor-regen-manim').style.display = 'none';
                document.getElementById('script-editor-save').style.display = 'inline-block';
            } else if (currentMode === 'full') {
                scriptEditorTextarea.value = JSON.stringify(currentParsedJson, null, 4);
                scriptEditorHint.innerText = "Edit the entire presentation JSON.";
                document.getElementById('script-editor-regen-feedback').style.display = 'none';
                document.getElementById('script-editor-regen-manim').style.display = 'none';
                document.getElementById('script-editor-save').style.display = 'inline-block';
            }
            scriptEditorModal.classList.add('active');
        }

        editorTabs.forEach(tab => {
            tab.addEventListener('click', (e) => {
                editorTabs.forEach(t => {
                    t.classList.remove('active-tab');
                    t.style.background = 'transparent';
                    t.style.color = '#aaa';
                    t.style.border = '1px solid #555';
                });
                
                const clickedTab = e.target;
                clickedTab.classList.add('active-tab');
                clickedTab.style.background = 'rgba(255, 215, 0, 0.2)';
                clickedTab.style.color = 'var(--text-gold)';
                clickedTab.style.border = '1px solid var(--border-gold)';
                
                editorMode = clickedTab.getAttribute('data-mode');
                populateEditor();
            });
        });

        editScriptBtn.addEventListener('click', () => {
            if (!slides || slides.length === 0) {
                alert("No script has been generated yet. Please generate a video first.");
                return;
            }
            
            // Pause TTS and Video so user can edit in peace
            window.speechSynthesis.pause();
            const aiVideo = document.getElementById('ai-video-player');
            if (aiVideo && !aiVideo.paused) {
                aiVideo.pause();
            }

            // Populate textarea with currently selected mode
            populateEditor();
            
            // Show Modal
        });

        scriptEditorCancel.addEventListener('click', () => {
            scriptEditorModal.classList.remove('active');
        });

        scriptEditorSave.addEventListener('click', () => {
            try {
                const rawText = scriptEditorTextarea.value;
                
                if (editorMode === 'slide') {
                    const updatedSlide = JSON.parse(rawText);
                    slides[currentSlide] = updatedSlide;
                } else if (editorMode === 'full') {
                    const updatedSlides = JSON.parse(rawText);
                    if (!Array.isArray(updatedSlides)) {
                        throw new Error("Parsed data is not an array. The script must be an array of scenes.");
                    }
                    slides = updatedSlides;
                } else if (editorMode === 'narration') {
                    slides[currentSlide].narration = rawText;
                }
                
                // Re-render the current slide to instantly show any HTML changes
                renderSlide(currentSlide);
                
                // Auto-save backup to ensure edits are not lost on refresh
                saveProjectBackup();
                
                scriptEditorModal.classList.remove('active');
                
                // Resume TTS and Video
                window.speechSynthesis.resume();
                const aiVideo = document.getElementById('ai-video-player');
                if (aiVideo && aiVideo.paused) {
                    aiVideo.play().catch(e => console.log("Video resume failed:", e));
                }
                
            } catch (err) {
                alert("Error saving! If you are editing JSON, please check your syntax. Error: " + err.message);
            }
        });

        const scriptEditorRegenManim = document.getElementById('script-editor-regen-manim');
        if (scriptEditorRegenManim) {
            scriptEditorRegenManim.addEventListener('click', async () => {
                const s = slides[currentSlide];
                let existingCode = "";
                if (s.type === 'simulation') existingCode = s.manim_code;
                else if (s.side_panel && s.side_panel.type === 'manim') existingCode = s.side_panel.manim_code;
                
                if (!existingCode) return;
                
                scriptEditorRegenManim.innerText = "Regenerating... (Wait)";
                scriptEditorRegenManim.disabled = true;
                
                const regenFeedbackInput = document.getElementById('script-editor-regen-feedback');
                const userFeedback = regenFeedbackInput ? regenFeedbackInput.value.trim() : "";
                
                try {
                    const res = await fetch('/regenerate-manim', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            slide_title: s.title,
                            narration: s.narration,
                            existing_code: existingCode,
                            user_feedback: userFeedback
                        })
                    });
                    
                    if (!res.ok) throw new Error("Failed to reach server.");
                    const data = await res.json();
                    if (data.status !== 'success') throw new Error(data.detail);
                    
                    // Replace the code
                    if (s.type === 'simulation') s.manim_code = data.manim_code;
                    else s.side_panel.manim_code = data.manim_code;
                    
                    // Update the textarea immediately
                    scriptEditorTextarea.value = JSON.stringify(s, null, 2);
                    
                    // Automatically save and render!
                    slides[currentSlide] = s;
                    saveProjectBackup(); // Save to local storage backup
                    
                    scriptEditorModal.classList.remove('active');
                    renderSlide(currentSlide);
                    
                    // Resume TTS and Video
                    window.speechSynthesis.resume();
                    const aiVideo = document.getElementById('ai-video-player');
                    if (aiVideo && aiVideo.paused) {
                        aiVideo.play().catch(e => console.log("Video resume failed:", e));
                    }
                } catch (e) {
                    alert("Error regenerating manim: " + e.message);
                } finally {
                    scriptEditorRegenManim.innerText = "Regenerate Manim";
                    scriptEditorRegenManim.disabled = false;
                }
            });
        }

        // --- Info Modal Logic ---
        const infoBtn = document.getElementById('info-btn');

        function populateInfoModal() {
            const infoModalList = document.getElementById('info-modal-list');
            infoModalList.innerHTML = '';
            if (!slides || slides.length === 0) {
                infoModalList.innerHTML = '<div style="color:#aaa; text-align:center; padding:2rem;">No presentation generated yet.</div>';
                return;
            }

            slides.forEach((slide, index) => {
                const row = document.createElement('div');
                row.style.display = 'flex';
                row.style.justifyContent = 'space-between';
                row.style.alignItems = 'center';
                row.style.padding = '10px';
                row.style.background = 'rgba(255,255,255,0.05)';
                row.style.borderRadius = '6px';

                const titleSection = document.createElement('div');
                titleSection.style.display = 'flex';
                titleSection.style.flexDirection = 'column';
                
                const titleText = document.createElement('span');
                titleText.style.color = '#fff';
                titleText.style.fontWeight = 'bold';
                titleText.innerText = `Slide ${index + 1}: ${slide.title || 'Untitled'}`;
                
                const typeText = document.createElement('span');
                typeText.style.color = '#aaa';
                typeText.style.fontSize = '0.8rem';
                typeText.innerText = `Type: ${slide.type}`;
                
                titleSection.appendChild(titleText);
                titleSection.appendChild(typeText);

                const selectContainer = document.createElement('div');
                const select = document.createElement('select');
                select.style.padding = '0.4rem';
                select.style.background = '#222';
                select.style.color = '#fff';
                select.style.border = '1px solid #555';
                select.style.borderRadius = '4px';
                select.style.cursor = 'pointer';

                const options = [
                    { value: 'auto', text: 'Auto (AI Decides)' },
                    { value: 'mascot', text: 'Force Aadhi (Mascot)' },
                    { value: 'tree', text: 'Force Concept Tree' }
                ];

                options.forEach(opt => {
                    const optionEl = document.createElement('option');
                    optionEl.value = opt.value;
                    optionEl.innerText = opt.text;
                    if (slide.force_background === opt.value) {
                        optionEl.selected = true;
                    }
                    select.appendChild(optionEl);
                });

                select.addEventListener('change', (e) => {
                    slide.force_background = e.target.value;
                    // Auto-save backup
                    saveProjectBackup();
                });

                selectContainer.appendChild(select);

                if (slide.type === 'ai_video') {
                    const audioSelect = document.createElement('select');
                    audioSelect.style.padding = '0.4rem';
                    audioSelect.style.background = '#222';
                    audioSelect.style.color = '#fff';
                    audioSelect.style.border = '1px solid #555';
                    audioSelect.style.borderRadius = '4px';
                    audioSelect.style.cursor = 'pointer';
                    audioSelect.style.marginLeft = '10px';

                    const audioOptions = [
                        { value: 'manual', text: 'Audio: Manual Voiceover' },
                        { value: 'video', text: 'Audio: Inside Video' }
                    ];

                    audioOptions.forEach(opt => {
                        const optionEl = document.createElement('option');
                        optionEl.value = opt.value;
                        optionEl.innerText = opt.text;
                        if (slide.ai_audio_source === opt.value) {
                            optionEl.selected = true;
                        }
                        audioSelect.appendChild(optionEl);
                    });

                    audioSelect.addEventListener('change', (e) => {
                        slide.ai_audio_source = e.target.value;
                        // Auto-save backup
                        saveProjectBackup();
                    });

                    selectContainer.appendChild(audioSelect);
                }

                // --- Media Upload Injector ---
                const mediaSection = document.createElement('div');
                mediaSection.style.display = 'flex';
                mediaSection.style.flexDirection = 'column';
                mediaSection.style.gap = '5px';
                mediaSection.style.marginLeft = 'auto';
                mediaSection.style.marginRight = '15px';

                // 1. Check for AI Video uploads
                if (slide.type === 'ai_video') {
                    const vidLabel = document.createElement('label');
                    vidLabel.style.fontSize = '0.8rem';
                    vidLabel.style.color = '#4CAF50';
                    vidLabel.style.cursor = 'pointer';
                    vidLabel.style.padding = '0.2rem 0.5rem';
                    vidLabel.style.border = '1px solid #4CAF50';
                    vidLabel.style.borderRadius = '4px';
                    vidLabel.innerText = slide.video_url ? 'Video Uploaded ✓' : 'Upload Video';
                    
                    const vidInput = document.createElement('input');
                    vidInput.type = 'file';
                    vidInput.accept = 'video/*';
                    vidInput.style.display = 'none';
                    
                    vidInput.addEventListener('change', async (e) => {
                        const file = e.target.files[0];
                        if(!file) return;
                        vidLabel.innerText = 'Uploading...';
                        const fd = new FormData();
                        fd.append('file', file);
                        try {
                            const res = await fetch('/upload-media', {method: 'POST', body: fd});
                            const data = await res.json();
                            slide.video_url = data.url;
                            vidLabel.innerText = 'Video Uploaded ✓';
                            saveProjectBackup();
                            if(currentSlide === index) renderSlide(currentSlide);
                        } catch(err) {
                            vidLabel.innerText = 'Upload Failed';
                            console.error(err);
                        }
                    });
                    vidLabel.appendChild(vidInput);
                    mediaSection.appendChild(vidLabel);
                }

                // 2. Check for Image Placeholders
                if (slide.html) {
                    const temp = document.createElement('div');
                    temp.innerHTML = slide.html;
                    const placeholders = temp.querySelectorAll('.board-image-placeholder');
                    if (!slide.uploaded_images) slide.uploaded_images = {};
                    
                    placeholders.forEach(ph => {
                        const imgId = ph.getAttribute('data-img-id');
                        const imgLabel = document.createElement('label');
                        imgLabel.style.fontSize = '0.8rem';
                        imgLabel.style.color = '#ff9800';
                        imgLabel.style.cursor = 'pointer';
                        imgLabel.style.padding = '0.2rem 0.5rem';
                        imgLabel.style.border = '1px solid #ff9800';
                        imgLabel.style.borderRadius = '4px';
                        imgLabel.innerText = slide.uploaded_images[imgId] ? `${imgId} ✓` : `Upload ${imgId}`;
                        
                        const imgInput = document.createElement('input');
                        imgInput.type = 'file';
                        imgInput.accept = 'image/*';
                        imgInput.style.display = 'none';
                        
                        imgInput.addEventListener('change', async (e) => {
                            const file = e.target.files[0];
                            if(!file) return;
                            imgLabel.innerText = 'Uploading...';
                            const fd = new FormData();
                            fd.append('file', file);
                            try {
                                const res = await fetch('/upload-media', {method: 'POST', body: fd});
                                const data = await res.json();
                                slide.uploaded_images[imgId] = data.url;
                                imgLabel.innerText = `${imgId} ✓`;
                                saveProjectBackup();
                            } catch(err) {
                                imgLabel.innerText = 'Upload Failed';
                                console.error(err);
                            }
                        });
                        imgLabel.appendChild(imgInput);
                        mediaSection.appendChild(imgLabel);
                    });
                }

                row.appendChild(titleSection);
                row.appendChild(mediaSection);
                row.appendChild(selectContainer);
                infoModalList.appendChild(row);
            });
        }

        infoBtn.addEventListener('click', () => {
            populateInfoModal();

            const infoModal = document.getElementById('info-modal');
            infoModal.classList.add('active');
        });

        document.getElementById('info-modal-close').addEventListener('click', () => {
            const infoModal = document.getElementById('info-modal');
            infoModal.classList.remove('active');
            renderSlide(currentSlide);
        });

        // --- Keyboard Shortcuts ---
        document.addEventListener('keydown', (e) => {
            if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;
            if (e.key.toLowerCase() === 'h') {
                const controlBar = document.getElementById('voice-control-bar');
                if (controlBar) {
                    controlBar.classList.toggle('hidden-force');
                }
            }
        });

        // --- Initialization Logic ---
        window.onload = () => {
            if (window.__EXPORTED_STATE__) {
                // We are in an exported HTML file!
                slides.length = 0;
                slides.push(...window.__EXPORTED_STATE__.slides);
                conceptMapData = window.__EXPORTED_STATE__.conceptMapData;
                currentSubjectName = window.__EXPORTED_STATE__.currentSubjectName;
                
                // Attempt to set voice dropdown if it still exists
                const voiceSelect = document.getElementById('voice-select');
                if (voiceSelect) {
                    voiceSelect.value = window.__EXPORTED_STATE__.selectedVoice;
                }
                
                // Hide input panel UI
                const inputPanel = document.querySelector('.input-container');
                if (inputPanel) inputPanel.style.display = 'none';

                // Automatically show the start overlay immediately instead of requiring generation
                showStartOverlay();
            } else {
                // Normal local mode: Check for saved backup in localStorage
                const backup = localStorage.getItem('saved_slides_backup');
                if (backup) {
                    try {
                        const parsedBackup = JSON.parse(backup);
                        if (parsedBackup.slides && parsedBackup.slides.length > 0) {
                            const restoreBtn = document.getElementById('restore-btn');
                            if (restoreBtn) {
                                restoreBtn.style.display = 'inline-block';
                                restoreBtn.addEventListener('click', () => {
                                    slides.length = 0;
                                    slides.push(...parsedBackup.slides);
                                    conceptMapData = parsedBackup.conceptMapData;
                                    currentSubjectName = parsedBackup.subjectName || "Recovered Presentation";
                                    showStartOverlay();
                                });
                            }
                        }
                    } catch (e) {
                        console.error("Failed to parse backup:", e);
                    }
                }
            }
        };

    