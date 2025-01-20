// main.js

// Глобальні змінні
let searchType = ''; // 'text' або 'image'
let currentOffset = 0;
let currentQuery = '';
let isLoading = false;
let hasMoreResults = true;
let currentImageFile; // Глобальна змінна для зображення

// Ініціалізація модального вікна глобально
let imageModal;

// Доступ до змінних конфігурації з window.appConfig
const serveImageBaseUrl = window.appConfig.serveImageBaseUrl;
const thumbnailsBaseUrl = window.appConfig.thumbnailsBaseUrl;
const maxResultsPerPage = window.appConfig.maxResultsPerPage;
const directories = window.appConfig.directories;

document.addEventListener("DOMContentLoaded", function() {
    // Ініціалізація активної вкладки
    var activeTab = localStorage.getItem('activeTab') || 'search';
    var tabToOpen = document.querySelector(`[onclick="openTab(event, '${activeTab}')"]`);
    if (tabToOpen) {
        tabToOpen.click();
    } else {
        document.querySelector('[onclick="openTab(event, \'search\')"]').click();
    }

    // Ініціалізація логів та прогресу
    loadLogs();
    autoUpdateLogs();
    updateScanStatus();  // Запуск функції для відстеження прогресу
	
    // Ініціалізуємо модальне вікно один раз
    const imageModalElement = document.getElementById('imageModal');
    imageModal = new bootstrap.Modal(imageModalElement);

    // Додаємо обробник для закриття модального вікна при закритті користувачем
    imageModalElement.addEventListener('hidden.bs.modal', function () {
        // Очищаємо джерело зображення, щоб уникнути мерехтіння при наступному відкритті
        document.getElementById('modalImage').src = '';
    });
	
    // Ініціалізація області завантаження зображення
    initUploadArea();

    // Ініціалізація кнопок додавання та видалення часу сканування
    initScanTimeControls();

    // Ініціалізація обробників подій для кнопок керування скануванням
    initScanControlButtons();

    // Обробник форми пошуку за текстом
    document.getElementById('text-search-form').addEventListener('submit', function(e) {
        e.preventDefault(); // Запобігаємо стандартній поведінці форми

        currentQuery = document.getElementById('text-query').value.trim();
        if (currentQuery === "") {
            alert("Будь ласка, введіть запит для пошуку.");
            return;
        }

        // Скидаємо пагінацію
        currentOffset = 0;
        hasMoreResults = true;
        isLoading = false;
        searchType = 'text'; // Встановлюємо тип пошуку

        // Очищаємо результати
        const resultsContainer = document.getElementById("results-container");
        resultsContainer.innerHTML = '';

        // Показуємо індикатор завантаження
        const loadingIndicator = document.getElementById('loading-indicator');
        loadingIndicator.style.display = 'block';

        // Виконуємо перший запит
        loadMoreResults();
    });

    // Функція для завантаження додаткових результатів
    function loadMoreResults() {
        if (isLoading || !hasMoreResults) return;

        if (searchType === 'text') {
            if (!currentQuery || currentQuery.trim() === '') return;
            isLoading = true;

            const loadingIndicator = document.getElementById('loading-indicator');
            loadingIndicator.style.display = 'block';

            let data = {
                text_query: currentQuery,
                offset: currentOffset,
                limit: maxResultsPerPage // Кількість результатів на сторінку
            };

            fetch('/search_text', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest' // Для ідентифікації AJAX-запиту
                },
                body: JSON.stringify(data)
            })
            .then(response => {
                loadingIndicator.style.display = 'none';
                isLoading = false;
                if (response.ok) {
                    return response.json();
                } else {
                    return response.json().then(data => { throw new Error(data.error || 'Помилка при пошуку за текстом.'); });
                }
            })
            .then(data => {
                if (data.results && data.results.length > 0) {
                    updateResults(data.results, false); // Додаємо результати до наявних
                    currentOffset += maxResultsPerPage; // Збільшуємо на limit
                    hasMoreResults = data.results.length === maxResultsPerPage; // Встановлюємо, чи є ще результати
                } else {
                    hasMoreResults = false;
                    console.log('Більше немає результатів.');
                }
            })
            .catch(error => {
                loadingIndicator.style.display = 'none';
                isLoading = false;
                console.error('Error during text search:', error);
                alert(error.message);
            });
        } else if (searchType === 'image') {
            if (!currentImageFile) return; // Перевірка наявності файлу
            isLoading = true;

            const loadingIndicator = document.getElementById('loading-indicator');
            loadingIndicator.style.display = 'block';

            const formData = new FormData();
            formData.append('offset', currentOffset);
            formData.append('limit', maxResultsPerPage);

            // Додаємо файл зображення, який зберігається в `currentImageFile`
            formData.append('image', currentImageFile);

            fetch('/search_image', {
                method: 'POST',
                body: formData,
                credentials: 'same-origin',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest' // Для ідентифікації AJAX-запиту
                }
            })
            .then(response => {
                loadingIndicator.style.display = 'none';
                isLoading = false;
                if (response.ok) {
                    return response.json();
                } else {
                    return response.json().then(data => { throw new Error(data.error || 'Помилка при пошуку зображення.'); });
                }
            })
            .then(data => {
                if (data.results && data.results.length > 0) {
                    updateResults(data.results, false); // Додаємо результати до наявних
                    currentOffset += maxResultsPerPage; // Збільшуємо на limit
                    hasMoreResults = data.results.length === maxResultsPerPage; // Встановлюємо, чи є ще результати
                } else {
                    hasMoreResults = false;
                    console.log('Більше немає результатів.');
                }
            })
            .catch(error => {
                loadingIndicator.style.display = 'none';
                isLoading = false;
                console.error('Error during image search:', error);
                alert(error.message);
            });
        }
    }

    // Обробник прокрутки сторінки для автоматичного завантаження
    window.addEventListener('scroll', () => {
        if (searchType === '') {
            // Не завантажуємо додаткові результати, якщо немає активного пошуку
            return;
        }
        if ((window.innerHeight + window.scrollY) >= document.body.offsetHeight - 500) {
            loadMoreResults();
        }
    });

    // Функція для оновлення результатів пошуку
    function updateResults(results, replace = true) {
        const resultsContainer = document.getElementById("results-container");
        if (replace) {
            if (!results || results.length === 0) {
                resultsContainer.innerHTML = `<p>Немає результатів для запиту "${currentQuery}".</p>`;
                return;
            }
            resultsContainer.innerHTML = ''; // Очищаємо контейнер
            currentOffset = 0;
            hasMoreResults = true;
        }

        let html = '<div class="row">'; // Додаємо відкриття ряду
        results.forEach(result => {
            let thumbnailUrl = thumbnailsBaseUrl + result.id + ".png";
            let fullImageUrl = serveImageBaseUrl + result.id;
            let isPSD = result.file_name.toLowerCase().endsWith('.psd');

            html += `
                <div class="col-lg-3 col-md-4 col-sm-6">
                    <div class="card mb-4 image-card">
                        <div class="image-container">
                            <img src="${thumbnailUrl}" 
                                 class="card-img-top" 
                                 alt="${result.file_name}" 
                                 data-image-url="${fullImageUrl}" />
                            <div class="info">
                                <p>ID: ${result.id}</p>
                                <p>Ім'я файлу: ${result.file_name}</p>
                                <p>Шлях до файлу: ${result.file_path.replace(/\\/g, '/')}</p>
                                <p>Схожість: ${result.similarity.toFixed(2)}</p>
								<button type="button" class="btn btn-info btn-sm open-folder-button" data-file-path="${result.file_path.replace(/\\/g, '/')}">Відкрити шлях</button>
                                ${isPSD ? `<a href="/download/${result.id}" class="btn btn-secondary btn-sm mt-2">Завантажити PSD</a>` : ''}
                            </div>
                        </div>
                    </div>
                </div>
            `;
        });
        html += '</div>'; // Додаємо закриття ряду

        resultsContainer.insertAdjacentHTML('beforeend', html);

        // Додаємо обробники подій до зображень
		const images = resultsContainer.querySelectorAll('.card-img-top');
		images.forEach(img => {
			img.addEventListener('click', function() {
				const imageUrl = this.getAttribute('data-image-url');
				showModal(imageUrl);
			});

			// Обробник події dragstart
			img.addEventListener('dragstart', function(e) {
				const imageUrl = this.getAttribute('data-image-url');
				e.dataTransfer.setData('text/plain', imageUrl);
			});
		});


        // Додаємо обробники подій до кнопок "Відкрити шлях"
        const openFolderButtons = resultsContainer.querySelectorAll('.open-folder-button');
        openFolderButtons.forEach(button => {
            button.addEventListener('click', function() {
                const filePath = this.getAttribute('data-file-path');
                openFolder(filePath);
            });
        });

        // Якщо більше немає результатів
        if (!hasMoreResults) {
            resultsContainer.insertAdjacentHTML('beforeend', '<p>Більше немає результатів.</p>');
        }
    }

    // Функція для видалення логів
    function clearLogs() {
        fetch('/clear_logs', { method: 'POST' })
            .then(response => {
                if (response.ok) {
                    document.getElementById("logs").value = "";
                }
            })
            .catch(error => console.error('Error clearing logs:', error));
    }

    // Функції для оновлення логів
    function loadLogs() {
        fetch('/get_logs')
            .then(response => response.text())
            .then(data => {
                var logsArea = document.getElementById("logs");
                logsArea.value = data;
                scrollToBottom();
            })
            .catch(error => console.error('Error loading logs:', error));
    }

    function scrollToBottom() {
        var logsArea = document.getElementById("logs");
        logsArea.scrollTop = logsArea.scrollHeight;
    }

    function autoUpdateLogs() {
        setInterval(loadLogs, 5000);
    }

    // Ініціалізація обробників подій для кнопок керування скануванням
    function initScanControlButtons() {
        directories.forEach((directory, index) => {
            const idx = index + 1; // Оскільки loop.index починається з 1

            // Обробник для кнопки "Сканувати"
            const startButton = document.getElementById(`start-scan-button-${idx}`);
            const stopButton = document.getElementById(`stop-scan-button-${idx}`);

            if (startButton) {
                startButton.addEventListener('click', function() {
                    console.log(`Starting scan for directory: ${directory}`);
                    toggleScan(directory, 'start', idx);
                });
            }

            // Обробник для кнопки "Зупинити"
            if (stopButton) {
                stopButton.addEventListener('click', function() {
                    console.log(`Stopping scan for directory: ${directory}`);
                    toggleScan(directory, 'stop', idx);
                });
            }
        });
    }

    // Функція для відправки запиту на сервер для керування скануванням
    function toggleScan(directory, action, idx) {
        console.log(`Sending toggle_scan request: action=${action}, directory=${directory}`);
        fetch('/toggle_scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ directory: directory, action: action })
        })
        .then(response => response.json())
        .then(data => {
            console.log(`Received response from toggle_scan:`, data);
            if (data.success) {
                // Оновлюємо інтерфейс після успішного запуску або зупинки сканування
                updateScanStatus();
            } else if (data.needs_resume) {
                // Показати модальне вікно для вибору відновлення або скасування
                showResumeModal(directory, idx);
            } else if (data.error) {
                console.error('Error toggling scan:', data.error);
                alert('Помилка при зміні стану сканування: ' + data.error);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Помилка при зміні стану сканування.');
        });
    }

    // Функція для показу модального вікна відновлення сканування
    function showResumeModal(directory, idx) {
        console.log(`Showing resume modal for directory: ${directory}`);
        // Встановлюємо значення прихованого поля у формі
        document.getElementById('modal-directory').value = directory;

        // Показуємо модальне вікно
        var resumeModal = new bootstrap.Modal(document.getElementById('resumeModal'));
        resumeModal.show();
    }

    // Функція для відстеження прогресу сканування та оновлення кнопок
    function updateScanStatus() {
        fetch('/get_scan_progress')
            .then(response => response.json())
            .then(data => {
                console.log('Received scan progress:', data);
                directories.forEach((directory, index) => {
                    const idx = index + 1;
                    const isActive = data.active_scans && data.active_scans.includes(directory);

                    // Оновлення кнопок керування скануванням
                    const startButton = document.getElementById(`start-scan-button-${idx}`);
                    const stopButton = document.getElementById(`stop-scan-button-${idx}`);

                    if (startButton && stopButton) {
                        if (isActive) {
                            startButton.style.display = 'none';
                            stopButton.style.display = 'inline-block';
                        } else {
                            startButton.style.display = 'inline-block';
                            stopButton.style.display = 'none';
                        }
                    }

                    // Оновлення прогрес-барів
                    const progress = data.scan_progress[directory] || 0;
                    const progressBar = document.getElementById(`progress-${idx}`);
                    if (progressBar) {
                        if (progress >= 0 && progress < 100) {
                            progressBar.style.width = `${progress}%`;
                            progressBar.setAttribute('aria-valuenow', progress);
                            progressBar.innerText = `${progress}%`;
                            progressBar.classList.remove('bg-danger', 'bg-success', 'bg-warning');
                            progressBar.classList.add('bg-info');
                        } else if (progress === 100) {
                            progressBar.style.width = `100%`;
                            progressBar.setAttribute('aria-valuenow', 100);
                            progressBar.innerText = `Завершено`;
                            progressBar.classList.remove('bg-danger', 'bg-info', 'bg-warning');
                            progressBar.classList.add('bg-success');
                        } else if (progress === -1) {
                            progressBar.style.width = `100%`;
                            progressBar.setAttribute('aria-valuenow', 100);
                            progressBar.innerText = `Помилка`;
                            progressBar.classList.remove('bg-info', 'bg-success', 'bg-warning');
                            progressBar.classList.add('bg-danger');
                        } else if (progress === -2) {
                            progressBar.style.width = `100%`;
                            progressBar.setAttribute('aria-valuenow', 100);
                            progressBar.innerText = `Зупинено`;
                            progressBar.classList.remove('bg-info', 'bg-success', 'bg-danger');
                            progressBar.classList.add('bg-warning');
                        }
                    }
                });

                // Повторюємо запит кожні 2 секунди
                setTimeout(updateScanStatus, 2000);
            })
            .catch(error => console.error('Error fetching scan progress:', error));
    }

    // Ініціалізація області завантаження зображення
    function initUploadArea() {
        const uploadArea = document.getElementById('upload-area');
        const imageInput = document.getElementById('image-input');
        const previewImage = document.getElementById('preview-image');
        const uploadError = document.getElementById('upload-error');
        const loadingIndicator = document.getElementById('loading-indicator');
        const resultsContainer = document.getElementById('results-container');

        let cropper; // Глобальна змінна для Cropper.js
        let currentFile; // Зберігаємо поточний файл для кадрування

        const cropModalElement = document.getElementById('cropModal');
        const cropModal = new bootstrap.Modal(cropModalElement);
        const cropButton = document.getElementById('crop-button');

        // Клік по області відкриває діалог вибору файлу
        uploadArea.addEventListener('click', () => {
            imageInput.click();
        });

        // Обробка вибору файлу
        imageInput.addEventListener('change', (e) => {
            handleFiles(e.target.files);
        });

        // Обробка перетягування файлів
        uploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadArea.classList.add('dragover');
        });

        uploadArea.addEventListener('dragleave', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('dragover');
        });

		uploadArea.addEventListener('drop', function(e) {
			e.preventDefault();
			uploadArea.classList.remove('dragover');

			const imageUrl = e.dataTransfer.getData('text/plain');
			if (imageUrl) {
				fetch(imageUrl)
					.then(response => response.blob())
					.then(blob => {
						const file = new File([blob], 'image.jpg', { type: blob.type });
						handleFiles([file]);
					})
					.catch(error => {
						console.error('Error fetching the image:', error);
					});
			} else {
				// Обробка файлів з файлової системи
				let files = [];
				if (e.dataTransfer.items) {
					for (let i = 0; i < e.dataTransfer.items.length; i++) {
						const item = e.dataTransfer.items[i];
						if (item.kind === 'file') {
							const file = item.getAsFile();
							files.push(file);
						}
					}
				} else if (e.dataTransfer.files) {
					files = e.dataTransfer.files;
				}
				handleFiles(files);
			}
		});


        // Функція для обробки файлів
        function handleFiles(files) {
            if (files.length > 0) {
                const file = files[0];
                if (file.type.startsWith('image/')) {
                    uploadError.style.display = 'none';
                    const reader = new FileReader();
                    reader.onload = function(event) {
                        previewImage.src = event.target.result;
                        previewImage.classList.add('active');
                        uploadArea.querySelector('span').style.display = 'none';
                        // Приховати попередні результати
                        resultsContainer.innerHTML = '';

                        // Ініціалізувати Cropper.js
                        const cropperImage = document.getElementById('cropper-image');
                        cropperImage.src = event.target.result;
                        cropModal.show();

                        currentFile = file; // Зберігаємо поточний файл
                    }
                    reader.readAsDataURL(file);
                } else {
                    uploadError.style.display = 'block';
                }
            }
        }

        // Ініціалізація Cropper.js при відкритті модального вікна
        cropModalElement.addEventListener('shown.bs.modal', function () {
            const image = document.getElementById('cropper-image');
            cropper = new Cropper(image, {
                aspectRatio: NaN, // Дозволяє вільний аспект
                viewMode: 1,
                autoCropArea: 1,
                movable: true,
                zoomable: true,
                rotatable: true,
                scalable: true,
            });
        });

        // Очистити Cropper.js при закритті модального вікна
        cropModalElement.addEventListener('hidden.bs.modal', function () {
            if (cropper) {
                cropper.destroy();
                cropper = null;
            }
        });

        // Обробник кнопки "Кадрувати та шукати"
        cropButton.addEventListener('click', function() {
            if (cropper) {
                // Отримати обрізане зображення як Blob
                cropper.getCroppedCanvas().toBlob(function(blob) {
                    // Створити новий файл з обрізаного зображення
                    const croppedFile = new File([blob], currentFile.name, { type: currentFile.type });

                    // Відправити обрізане зображення на сервер
                    submitCroppedImage(croppedFile);

                    // Відобразити обрізане зображення на попередньому перегляді
                    const reader = new FileReader();
                    reader.onload = function(e) {
                        previewImage.src = e.target.result;
                    }
                    reader.readAsDataURL(blob);

                    // Очистити Cropper.js та сховати модальне вікно
                    cropper.destroy();
                    cropper = null;
                    cropModal.hide();
                }, currentFile.type);
            }
        });

        // Функція для відправки обрізаного зображення
        function submitCroppedImage(file) {
            currentOffset = 0;
            hasMoreResults = true;
            isLoading = false;
            searchType = 'image'; // Встановлюємо тип пошуку

            // Зберігаємо файл зображення в глобальну змінну
            currentImageFile = file;

            const formData = new FormData();
            formData.append('image', file);
            formData.append('offset', currentOffset);
            formData.append('limit', maxResultsPerPage);

            const loadingIndicator = document.getElementById('loading-indicator');
            loadingIndicator.style.display = 'block';

            fetch('/search_image', {
                method: 'POST',
                body: formData,
                credentials: 'same-origin',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest' // Для ідентифікації AJAX-запиту
                }
            })
            .then(response => {
                // При успішній відправці приховати індикатор завантаження
                loadingIndicator.style.display = 'none';
                if (response.ok) {
                    return response.json();
                } else {
                    return response.json().then(data => { throw new Error(data.error || 'Помилка при пошуку зображення.'); });
                }
            })
            .then(data => {
                if (data.results && data.results.length > 0) {
                    updateResults(data.results, true); // Замінюємо попередні результати
                    currentOffset += maxResultsPerPage; // Збільшуємо на limit
                    hasMoreResults = data.results.length === maxResultsPerPage; // Встановлюємо, чи є ще результати
                } else {
                    hasMoreResults = false;
                    console.log('Пошук завершено.');
                }
            })
            .catch(error => {
                loadingIndicator.style.display = 'none';
                console.error('Error during image search:', error);
                alert(error.message);
            });
        }
    }

    // Функція для отримання та відображення статистики файлів
    function loadFileStats() {
        fetch('/file_stats')
            .then(response => response.json())
            .then(data => {
                // Оновлюємо загальну кількість файлів
                document.getElementById('total-files').innerText = data.total_files;

                // Оновлюємо кількість файлів за форматами
                const filesByFormatList = document.getElementById('files-by-format');
                filesByFormatList.innerHTML = ''; // Очищаємо список
                for (const [format, count] of Object.entries(data.files_by_format)) {
                    const listItem = document.createElement('li');
                    listItem.className = 'list-group-item d-flex justify-content-between align-items-center';
                    listItem.innerHTML = `
                        <span><i class="fas fa-file"></i> ${format.toUpperCase()}</span>
                        <span class="badge bg-secondary">${count}</span>
                    `;
                    filesByFormatList.appendChild(listItem);
                }

                // Оновлюємо кількість файлів, доданих сьогодні
                document.getElementById('files-today').innerText = data.files_today;

                // Оновлюємо найпопулярніший формат файлів
                document.getElementById('most-common-format').innerText = data.most_common_format ? data.most_common_format.toUpperCase() : '-';
            })
            .catch(error => {
                console.error('Помилка при отриманні статистики файлів:', error);
            });
    }

    // Викликаємо функцію при завантаженні сторінки
    loadFileStats();

    // Оновлюємо статистику кожні 60 секунд
    setInterval(loadFileStats, 60000); // Оновлення кожні 60 секунд

    // Ініціалізація кнопок додавання та видалення часу сканування
    function initScanTimeControls() {
        document.getElementById('add-time-button').addEventListener('click', function() {
            var container = document.getElementById('scan-times-container');
            var entry = document.createElement('div');
            entry.className = 'scan-time-entry';
            entry.innerHTML = `
                <input type="time" name="scan_times">
                <button type="button" class="remove-time-button">Видалити</button>
            `;
            container.appendChild(entry);
        });

        document.getElementById('scan-times-container').addEventListener('click', function(e) {
            if (e.target && e.target.classList.contains('remove-time-button')) {
                var scanTimesContainer = document.getElementById('scan-times-container');
                // Перевірка на кількість полів
                if (scanTimesContainer.getElementsByClassName('scan-time-entry').length > 1) {
                    e.target.parentElement.remove();
                } else {
                    alert("Повинен бути принаймні один час сканування.");
                }
            }
        });
    }
});

// Функція для показу модального вікна з повним зображенням
function showModal(imageUrl) {
    document.getElementById('modalImage').src = imageUrl;
    imageModal.show();
}

// Функція для переключення вкладок
function openTab(evt, tabName) {
    var i, tabcontent, tablinks;
    tabcontent = document.getElementsByClassName("tab-content");
    for (i = 0; i < tabcontent.length; i++) {
        tabcontent[i].style.display = "none";
    }
    tablinks = document.getElementsByClassName("nav-link");
    for (i = 0; i < tablinks.length; i++) {
        tablinks[i].className = tablinks[i].className.replace(" active", "");
    }
    document.getElementById(tabName).style.display = "block";
    evt.currentTarget.className += " active";
    localStorage.setItem('activeTab', tabName);
}

// Функція для відкриття папки
function openFolder(filePath) {
    // Екодуємо шлях, щоб уникнути проблем з символами
    const encodedPath = encodeURIComponent(filePath);
    fetch(`/open_folder?path=${encodedPath}`, {
        method: 'GET',
        headers: {
            'X-Requested-With': 'XMLHttpRequest'
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            console.log(`Folder opened: ${filePath}`);
        } else if (data.error) {
            console.error(`Error opening folder: ${data.error}`);
            alert(`Помилка при відкритті папки: ${data.error}`);
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Помилка при відкритті папки.');
    });
}


// Отримуємо кнопку
const scrollToTopBtn = document.getElementById('scrollToTopBtn');

// Обробник події прокручування
window.addEventListener('scroll', function() {
    if (window.pageYOffset > 300) {
        scrollToTopBtn.style.display = 'block';
    } else {
        scrollToTopBtn.style.display = 'none';
    }
});

// Обробник кліку на кнопку
scrollToTopBtn.addEventListener('click', function() {
    window.scrollTo({
        top: 0,
        behavior: 'smooth'
    });
});
