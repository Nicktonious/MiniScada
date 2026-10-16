import { useState } from 'react';
import styles from './Login.module.css';

export default function Login({ onSubmit, isLoading = false, error = '' }) {
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');

  const handleSubmit = (event) => {
    // Best practice 1: preventDefault()
    // Отменяем стандартное поведение браузера (полную перезагрузку страницы при отправке формы).
    event.preventDefault();

    if (onSubmit) {
      onSubmit({ login, password });
    }
  };

  return (
    <div className={styles.container}>
      {/* 
        Best practice 2: Тег <form> вместо обычного <div>
        Позволяет пользователю отправлять форму нажатием клавиши Enter с любого поля,
        а также обеспечивает доступность (accessibility) для скринридеров.
      */}
      <form className={styles.form} onSubmit={handleSubmit}>
        <h2 className={styles.title}>Вход в систему</h2>

        {/* Условный рендеринг ошибки: блок появится только если в пропс передан текст */}
        {error && <div className={styles.error}>{error}</div>}

        <div className={styles.inputGroup}>
          {/* 
            Best practice 3: Атрибут htmlFor + id
            Связывает <label> с конкретным <input>. При клике по тексту "Логин"
            курсор автоматически перемещается в соответствующее поле ввода.
          */}
          <label htmlFor="login-input" className={styles.label}>
            Логин
          </label>
          <input
            id="login-input"
            name="username"
            type="text"
            className={styles.input}
            value={login}
            onChange={(e) => setLogin(e.target.value)}
            placeholder="Введите логин"
            /*
              Best practice 4: autoComplete="username"
              Подсказывает браузеру и встроенным менеджерам паролей, что в это поле
              нужно предлагать сохраненные логины.
            */
            autoComplete="username"
            /*
              Best practice 5: required
              Включает нативную браузерную проверку на заполненность поля перед отправкой,
              не давая отправить пустые данные.
            */
            required
            disabled={isLoading}
          />
        </div>

        <div className={styles.inputGroup}>
          <label htmlFor="password-input" className={styles.label}>
            Пароль
          </label>
          <input
            id="password-input"
            name="password"
            type="password"
            className={styles.input}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Введите пароль"
            /*
              Best practice 6: autoComplete="current-password"
              Явно сообщает браузеру, что это текущий пароль учетной записи,
              позволяя быстро подставить его в связке с логином.
            */
            autoComplete="current-password"
            required
            disabled={isLoading}
          />
        </div>

        {/* 
          Best practice 7: Явный type="submit" и disabled={isLoading}
          Кнопка с type="submit" инициирует событие onSubmit у родительской формы.
          disabled во время загрузки предотвращает случайные повторные клики (double submit).
        */}
        <button
          type="submit"
          className={styles.submitBtn}
          disabled={isLoading}
        >
          {isLoading ? 'Загрузка...' : 'Войти'}
        </button>
      </form>
    </div>
  );
}