/*************************************************************************
 * Copyright (c) 2026 Graphviz contributors
 * All rights reserved. This program and the accompanying materials
 * are made available under the terms of the Eclipse Public License v2.0
 * which accompanies this distribution, and is available at
 * https://www.eclipse.org/org/documents/epl-2.0/EPL-2.0.html
 *
 * Contributors: Details at https://graphviz.org
 *************************************************************************/

#pragma once

#include <QDir>
#include <QFileInfo>
#include <QSettings>
#include <QString>

namespace gvedit {

inline QString lastFileDialogDirectory() {
  QSettings settings(QStringLiteral("Graphviz"), QStringLiteral("gvedit"));
  return settings
      .value(QStringLiteral("lastFileDialogDirectory"), QDir::homePath())
      .toString();
}

inline QString fileDialogPath(const QString &fileName = QString()) {
  QDir dir(lastFileDialogDirectory());
  if (fileName.isEmpty())
    return dir.absolutePath();

  return dir.filePath(QFileInfo(fileName).fileName());
}

inline void rememberFileDialogDirectory(const QString &fileName) {
  if (fileName.isEmpty())
    return;

  const QString path = QFileInfo(fileName).absoluteDir().absolutePath();
  if (path.isEmpty())
    return;

  QSettings settings(QStringLiteral("Graphviz"), QStringLiteral("gvedit"));
  settings.setValue(QStringLiteral("lastFileDialogDirectory"), path);
}

} // namespace gvedit
