#!/bin/bash

# Ім'я віртуального середовища
VENV_DIR="venv"

# Перевіряємо, чи встановлений Python
if ! command -v python3 &> /dev/null
then
    echo "Python3 не встановлено. Встановіть Python3 перед запуском скрипту."
    exit 1
fi

# Перевіряємо, чи встановлено virtualenv, якщо ні - встановлюємо
if ! python3 -m venv --help > /dev/null 2>&1; then
    echo "Встановлюємо модуль venv для Python..."
    sudo apt-get install python3-venv -y  # Для Ubuntu/Debian. Або відповідно для інших дистрибутивів
fi

# Створюємо віртуальне середовище, якщо його не існує
if [ ! -d "$VENV_DIR" ]; then
    echo "Створюємо віртуальне середовище в директорії $VENV_DIR"
    python3 -m venv $VENV_DIR
else
    echo "Віртуальне середовище вже існує в директорії $VENV_DIR"
fi

# Активуємо віртуальне середовище
source $VENV_DIR/bin/activate

# Оновлюємо pip та встановлюємо необхідні пакети
echo "Оновлюємо pip та встановлюємо залежності..."
pip install --upgrade pip
pip install -r requirements.txt

# Повідомляємо про успішну установку
echo "Залежності встановлено. Для запуску програми активуйте віртуальне середовище за допомогою:"
echo "source $VENV_DIR/bin/activate"
#python3 app.py
# Виходимо з віртуального середовища
deactivate
