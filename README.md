# PulsePlan

**PulsePlan** — это веб-приложение на Django для планирования тренировок, учёта упражнений и калорий, истории результатов подходов и шаблонов тренировок. Пользователь может выгрузить свои данные или безопасно удалить их.

Django-проект находится в директории `PulsePlanApp/`. По умолчанию используется база данных SQLite, а настройки приложения загружаются из переменных окружения.

## Содержание

- [Требования](#требования)
- [Локальная установка](#локальная-установка)
- [Настройка окружения](#настройка-переменных-окружения)
- [База данных и запуск сервера](#база-данных-и-запуск-сервера)
- [Production за Nginx](#production-https-за-nginx)
  - [Первичное развёртывание](#подготовка-чистой-копии)
  - [Обновление сайта без прав администратора](#обновление-существующего-сайта-без-прав-администратора)
  - [Проверка опубликованного сайта](#проверка-опубликованного-сайта)
- [Тестирование](#тестирование)
- [Демонстрационные данные](#дополнительные-демонстрационные-данные)
- [Материалы проекта](#материалы-проекта)

## Требования

- Python 3.12 или новее.
- Django 6.1.3 (указан в `PulsePlanApp/requirements.txt`).
- Браузер. Стили и скрипты Bootstrap 5.3.3 поставляются вместе с проектом.

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

### Почта для сброса пароля

В `.env.example` для локальной разработки включён `console.EmailBackend`: письма выводятся в консоль и не отправляются адресату. Для production замените эту строку в `.env` на SMTP-настройки провайдера:

```dotenv
DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
DJANGO_EMAIL_HOST=<SMTP-сервер>
DJANGO_EMAIL_PORT=587
DJANGO_EMAIL_HOST_USER=<имя-пользователя-SMTP>
DJANGO_EMAIL_HOST_PASSWORD=<пароль-или-ключ-SMTP>
DJANGO_EMAIL_USE_TLS=True
DJANGO_EMAIL_USE_SSL=False
DJANGO_DEFAULT_FROM_EMAIL=PulsePlan <noreply@ваш-домен>
```

Именно замените активную строку `DJANGO_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend`, а не добавляйте вторую: при дублировании переменных локальный загрузчик `.env` может оставить первое значение. SMTP-реквизиты выдаёт почтовый провайдер; не публикуйте их и не добавляйте в Git. После настройки проверьте запрос сброса пароля и папку «Спам».

При `DJANGO_DEBUG=False` Django доверяет `X-Forwarded-Proto: https`, перенаправляет HTTP на HTTPS и выставляет secure-флаги для session и CSRF cookies. Не включайте этот proxy-заголовок, если приложение доступно клиентам в обход настроенного Nginx. Не используйте `runserver` для production.

### Обновление существующего сайта без прав администратора

Следующие шаги предназначены для обновления уже работающего сайта от пользователя `pulseplan`. Они не требуют `sudo` и не удаляют локальные настройки, базу или собранную статику. Если любая проверка не проходит, остановитесь и согласуйте дальнейшие действия с администратором.

Подключитесь к серверу и перейдите в корень репозитория:

```sh
ssh pulseplan@<адрес-сервера>
cd /srv/student-projects/pulseplan
```

Сначала проверьте рабочую копию и обновления на GitHub:

```sh
git status --short --branch
git fetch origin
git diff --name-status HEAD..origin/main
```

Если есть изменения отслеживаемых файлов в рабочей копии, не переключайте ветки и не обновляйте код, пока эти изменения не будут разобраны. Не запускайте `git reset --hard` или `git clean`: неотслеживаемые `.env`, резервные копии базы и `staticfiles/` нужно сохранить. Если список обновляемых файлов включает `PulsePlanApp/config/settings.py`, отдельно сохраните текущий файл и проверьте production-параметры `.env`; не восстанавливайте старый файл поверх новой версии без проверки.

```sh
cd /srv/student-projects/pulseplan/PulsePlanApp
umask 077
python3 - <<'PY'
import sqlite3
from datetime import datetime
from pathlib import Path

source = sqlite3.connect("db.sqlite3")
backup_path = Path.home() / f"db.sqlite3.pre-update-{datetime.now():%Y%m%d-%H%M%S}"
target = sqlite3.connect(backup_path)
source.backup(target)
target.close()
source.close()
print(f"Резервная копия создана: {backup_path}")
PY
```

Перед обновлением убедитесь, что `.env` содержит production-настройки, не выводя его содержимое в терминал или чат:

```dotenv
DJANGO_SECRET_KEY=<длинный-случайный-секретный-ключ>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=pulseplan.treidendev.ru
DJANGO_CSRF_TRUSTED_ORIGINS=https://pulseplan.treidendev.ru
```

Если в обновлении затрагивается `config/settings.py`, можно сохранить текущий файл с закрытыми правами до `git pull`:

```sh
umask 077
cp config/settings.py "$HOME/pulseplan-settings.py.pre-update-$(date +%Y%m%d-%H%M%S)"
```

Затем обновите код только fast-forward-операцией, установите зависимости в виртуальное окружение и выполните проверки, миграции и сборку статики:

```sh
cd /srv/student-projects/pulseplan
git pull --ff-only origin main

cd /srv/student-projects/pulseplan/PulsePlanApp
source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py check --deploy
python manage.py migrate --noinput
umask 022
python manage.py collectstatic --noinput
```

После резервного копирования с `umask 077` восстановите `umask 022` перед `collectstatic`, чтобы Nginx мог читать новые статические файлы. Содержимое `staticfiles/` не удаляйте. Оцените все сообщения `check --deploy`. Предупреждение `security.W004` об отключённом HSTS ожидаемо для текущей конфигурации; не включайте HSTS без отдельной проверки постоянного HTTPS для домена и поддоменов. Не продолжайте при ошибках проверок, миграций или сборки статики.

Убедитесь, что Nginx отдаёт ключевой CSS-файл:

```sh
curl --fail -sS -o /dev/null -w 'Bootstrap CSS HTTP %{http_code}\n' \
  https://pulseplan.treidendev.ru/static/workout/vendor/bootstrap.min.css
```

Ожидается `Bootstrap CSS HTTP 200`; при другом ответе проверьте права на `staticfiles/` и не продолжайте до исправления.

Если новые файлы созданы с закрытыми правами и принадлежат пользователю `pulseplan`, восстановите права на чтение для Nginx и повторите проверку:

```sh
find staticfiles -type d -exec chmod 755 {} +
find staticfiles -type f -exec chmod 644 {} +
```

Если изменить права не удаётся, остановитесь и обратитесь к администратору сервера.

Для graceful reload без `sudo` сначала проверьте PID главного процесса systemd-службы и его владельца:

```sh
pid=$(systemctl show pulseplan.service -p MainPID --value)
ps -o pid=,user:20=,comm= -p "$pid"
```

Продолжайте только если вывод показывает пользователя `pulseplan` и процесс `gunicorn`. В противном случае не отправляйте сигнал и попросите администратора перезапустить службу. Если владелец и процесс совпадают:

```sh
kill -HUP "$pid"
systemctl is-active pulseplan.service
```

Ожидаемый статус — `active`. `HUP` просит master-процесс Gunicorn штатно перезапустить workers и перечитать код, но не перезапускает сам master и не заменяет полноценный перезапуск после обновления пакета Gunicorn. Если команда недоступна или завершается ошибкой, не пытайтесь обходить ограничения прав: обратитесь к администратору, чтобы он выполнил перезапуск systemd-службы.

Проверьте страницу входа с сервера:

```sh
curl --fail -sS -o /dev/null -w 'HTTP %{http_code}\n' https://pulseplan.treidendev.ru/login/
```

Ожидаемый ответ — `HTTP 200`. Если проверка не проходит, не удаляйте резервную копию; проверьте доступные вам логи и согласуйте восстановление с администратором. Если браузер показывает старую статику, выполните жёсткую перезагрузку страницы.

### Что согласовать с администратором Nginx

- DNS `pulseplan.treidendev.ru` должен вести на сервер; сертификат TLS должен быть установлен и проверен.
- Nginx завершает HTTPS, перенаправляет HTTP на HTTPS и проксирует приложение на `127.0.0.1:8000`.
- Для upstream Nginx должен передавать исходный `Host`, `X-Forwarded-For` и выставлять `X-Forwarded-Proto $scheme`. Входящий от клиента `X-Forwarded-Proto` нужно перезаписывать, а не принимать как доверенный.
- Порт Gunicorn не должен быть открыт извне. Nginx должен отдавать собранную статику из `PulsePlanApp/staticfiles/` по URL-префиксу `/static/`.
- Конфигурации Nginx и systemd, пользователь процесса, права на SQLite и резервное копирование согласуются с администратором сервера; этот репозиторий не управляет ими. Без административных прав graceful reload возможен только если главный процесс Gunicorn принадлежит пользователю `pulseplan`; иначе перезапуск должен выполнить администратор.

### Проверка опубликованного сайта

После настройки Nginx/systemd проверьте с клиентской машины:

```sh
curl -I http://pulseplan.treidendev.ru/
curl -I https://pulseplan.treidendev.ru/
curl -I https://pulseplan.treidendev.ru/login/
```

Первый запрос должен перенаправлять на HTTPS; запрос к корню сайта без авторизации ожидаемо перенаправляется на страницу входа. Запрос `/login/` должен вернуть `200 OK`. В браузере проверьте вход тестовой учётной записью, защищённое действие с формой и отсутствие mixed content. Ошибка CSRF обычно означает, что Nginx передаёт неверный `Host`/`X-Forwarded-Proto` либо `DJANGO_CSRF_TRUSTED_ORIGINS` не соответствует HTTPS-origin. Не вводите реальные пароли в тестовые команды и не используйте production-учётную запись для проверки.

## Тестирование

Активируйте виртуальное окружение и настройте `.env`, как описано выше. Из директории `PulsePlanApp/` запустите:

```sh
python manage.py test workout
```

Django сам находит все три модуля приложения и создаёт отдельную тестовую базу. Команда `python manage.py test` запускает все тесты проекта и используется в CI.

| Что проверить | Команда |
| --- | --- |
| Основной набор приложения | `python manage.py test workout.tests` |
| HTTPS и настройки безопасности | `python manage.py test workout.test_deployment_security` |
| Повторную отправку форм, календарь, регистрацию и восстановление пароля | `python manage.py test workout.test_release_qa` |

Для проверки в браузере запустите `python manage.py runserver` и войдите под тестовым аккаунтом. На узком экране откройте тренировку из календаря, затем смените день и нажмите «Удалить» у тестовой тренировки. Должен открыться диалог с возможностью отмены. Включите ограничение скорости в инструментах разработчика, отправьте форму добавления и проверьте, что кнопка недоступна до получения ответа.

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
