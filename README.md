# PulsePlan

**PulsePlan** — это веб-приложение на Django для планирования тренировок, учёта упражнений и калорий, истории результатов подходов и шаблонов тренировок. Пользователь может выгрузить свои данные или безопасно удалить их.

Django-проект находится в директории `PulsePlanApp/`. По умолчанию используется база данных SQLite, а настройки приложения загружаются из переменных окружения.

## Требования

- Python 3.12 или новее.
- Django 6.1.3 (указан в `PulsePlanApp/requirements.txt`).
- Браузер с доступом к интернету для загрузки стилей и скриптов Bootstrap 5.3.3 через CDN.

Для локальной разработки не требуется отдельная база данных или дополнительный frontend-сервис.

## Локальная установка

Выполните следующие команды из корневой директории репозитория:

```sh
cd PulsePlanApp
python3 -m venv .venv
```

Активируйте виртуальное окружение:

```sh
# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Установите зависимости проекта:

```sh
python -m pip install -r requirements.txt
```

## Настройка переменных окружения

Создайте локальный файл настроек на основе примера:

```sh
cp .env.example .env
```

Сгенерируйте секретный ключ Django:

```sh
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Укажите полученное значение в переменной `DJANGO_SECRET_KEY` в файле `PulsePlanApp/.env`.

**Важно:** не добавляйте файл `.env` в Git и не используйте демонстрационный ключ из примера в качестве настоящего секретного ключа.

Для локальной разработки укажите следующие параметры:

```dotenv
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
```

По умолчанию `DJANGO_DEBUG` имеет значение `False`.

Если режим DEBUG отключён, переменная `DJANGO_ALLOWED_HOSTS` должна содержать имена хостов, с которых будет доступно приложение. Несколько значений указываются через запятую.

**Не включайте DEBUG в рабочей (production) среде.**

## База данных и запуск сервера

Находясь в директории `PulsePlanApp/` с активированным виртуальным окружением, выполните миграции базы данных:

```sh
python manage.py migrate
```

Запустите локальный сервер разработки:

```sh
python manage.py runserver
```

Откройте приложение в браузере по адресу:

[http://127.0.0.1:8000/](http://127.0.0.1:8000/)

По умолчанию используется база данных SQLite, расположенная по пути `PulsePlanApp/db.sqlite3`.

## Production HTTPS за Nginx

Ниже приведён порядок развёртывания для `pulseplan.treidendev.ru`. Приложение использует SQLite, поэтому серверный пользователь Gunicorn должен иметь право записи в `PulsePlanApp/db.sqlite3` и каталог с базой. Файл `.env` и база данных не должны попадать в Git или быть доступны напрямую через Nginx.

### Подготовка чистой копии

На Linux-сервере войдите под отдельным непривилегированным пользователем приложения и получите чистую копию проекта:

```sh
git clone https://github.com/Zstviii/PulsePlan.git PulsePlan
cd PulsePlan/PulsePlanApp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Сгенерируйте секретный ключ и внесите его в `.env` локальным редактором. Не передавайте ключ в командной строке сервера и не публикуйте его:

```sh
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Для production укажите в `.env`:

```dotenv
DJANGO_SECRET_KEY=<сгенерированный-секретный-ключ>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=pulseplan.treidendev.ru
DJANGO_CSRF_TRUSTED_ORIGINS=https://pulseplan.treidendev.ru
```

При `DJANGO_DEBUG=False` Django доверяет `X-Forwarded-Proto: https`, перенаправляет HTTP на HTTPS и выставляет secure-флаги для session и CSRF cookies. Не включайте этот proxy-заголовок, если приложение доступно клиентам в обход настроенного Nginx. Не используйте `runserver` для production.

### Проверки и запуск Django

Из каталога `PulsePlanApp/` с активированным виртуальным окружением выполните:

```sh
python manage.py check --deploy
python manage.py migrate --noinput
python manage.py collectstatic --noinput
gunicorn --bind 127.0.0.1:8000 config.wsgi:application
```

`check --deploy` выводит предупреждения о незаданных production-параметрах, которые нужно оценить перед публикацией. Предупреждение `security.W004` ожидаемо: HSTS здесь намеренно не включён, пока отдельно не подтверждено постоянное HTTPS-обслуживание домена и нужных поддоменов. Gunicorn здесь запускается на loopback-интерфейсе только для проверки; для постоянного запуска администратор должен согласовать и настроить systemd unit и права пользователя сервиса.

### Что согласовать с администратором Nginx

- DNS `pulseplan.treidendev.ru` должен вести на сервер; сертификат TLS должен быть установлен и проверен.
- Nginx завершает HTTPS, перенаправляет HTTP на HTTPS и проксирует приложение на `127.0.0.1:8000`.
- Для upstream Nginx должен передавать исходный `Host`, `X-Forwarded-For` и выставлять `X-Forwarded-Proto $scheme`. Входящий от клиента `X-Forwarded-Proto` нужно перезаписывать, а не принимать как доверенный.
- Порт Gunicorn не должен быть открыт извне. Nginx должен отдавать собранную статику из `PulsePlanApp/staticfiles/` по URL-префиксу `/static/`.
- Конфигурации Nginx и systemd, пользователь процесса, права на SQLite и резервное копирование согласуются с администратором сервера; этот репозиторий не управляет ими.

### Проверка опубликованного сайта

После настройки Nginx/systemd проверьте с клиентской машины:

```sh
curl -I http://pulseplan.treidendev.ru/
curl -I https://pulseplan.treidendev.ru/
curl -I https://pulseplan.treidendev.ru/login/
```

Первый запрос должен перенаправлять на HTTPS; запрос к корню сайта без авторизации ожидаемо перенаправляется на страницу входа. Запрос `/login/` должен вернуть `200 OK`. В браузере проверьте вход тестовой учётной записью, защищённое действие с формой и отсутствие mixed content. Ошибка CSRF обычно означает, что Nginx передаёт неверный `Host`/`X-Forwarded-Proto` либо `DJANGO_CSRF_TRUSTED_ORIGINS` не соответствует HTTPS-origin. Не вводите реальные пароли в тестовые команды и не используйте production-учётную запись для проверки.

## Тестирование

Для запуска тестов приложения тренировок выполните команду из директории `PulsePlanApp/`:

```sh
python manage.py test workout
```

## Дополнительные демонстрационные данные

Скрипт заполнения базы данных демонстрационными данными предназначен исключительно для локальной разработки.

Скрипт отказывается запускаться, если режим DEBUG не включён.

Чтобы создать демонстрационную учётную запись, добавьте приватный пароль в переменную `PULSEPLAN_DEMO_PASSWORD` в файле `PulsePlanApp/.env`.

После этого выполните команду:

```sh
python manage.py shell < seed_pulseplan.py
```

Скрипт не использует встроенный демонстрационный пароль.

## Материалы проекта

- [Таблица управления проектом](https://docs.google.com/spreadsheets/d/1gYbjhKysS02zGzOGWOWPD6OaY8_F39cGF2mvofauItA/edit?usp=sharing)
- [Документ с вопросами заказчику](https://docs.google.com/document/d/10MdsjD-B7TaYfk0DCkMoeQ3Blsi4Ykh-3OJIkwwXGEU/edit?usp=drivesdk)