/**
 * main.js — Electron main process for ORION Desktop Widget.
 *
 * Creates a frameless, transparent, always-on-top overlay window
 * positioned at the bottom-right of the primary display.
 */
const { app, BrowserWindow, globalShortcut, ipcMain, screen } = require('electron');
const path = require('path');

let mainWindow = null;

function createWindow() {
  const display = screen.getPrimaryDisplay();
  const { width, height } = display.workAreaSize;

  mainWindow = new BrowserWindow({
    width: 420,
    height: 600,
    x: width - 450,
    y: height - 660,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: false,
    resizable: false,
    hasShadow: false,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
    },
  });

  mainWindow.loadFile('index.html');

  // Make the window ignore mouse events on transparent areas
  // but still capture on the bubble/panel
  mainWindow.setIgnoreMouseEvents(false);

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(() => {
  createWindow();

  // Global shortcut: Ctrl+Space toggles the panel
  globalShortcut.register('Control+Space', () => {
    if (mainWindow) {
      mainWindow.webContents.send('toggle-panel');
    }
  });
});

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});

app.on('window-all-closed', () => {
  app.quit();
});

// IPC: Allow renderer to drag the window
ipcMain.on('window-drag', (event, { deltaX, deltaY }) => {
  if (mainWindow) {
    const [x, y] = mainWindow.getPosition();
    mainWindow.setPosition(x + deltaX, y + deltaY);
  }
});

// IPC: Minimize to tray
ipcMain.on('minimize-window', () => {
  if (mainWindow) mainWindow.minimize();
});
