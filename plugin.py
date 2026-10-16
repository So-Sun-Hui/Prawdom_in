# SPDX-License-Identifier: GPL-2.0-or-later
import json
import math
import os
import traceback
from datetime import datetime
from pathlib import Path

from qgis.PyQt.QtCore import Qt, QVariant, QStandardPaths, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtWidgets import (QAction, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QPlainTextEdit, QComboBox, QDoubleSpinBox, QCheckBox, QFileDialog,
    QLineEdit, QMessageBox, QProgressBar, QGroupBox)
from qgis.core import (QgsApplication, QgsTask, QgsProject, QgsGeometry,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY,
    QgsRectangle,
    QgsVectorLayer, QgsFeature, QgsField, QgsVectorFileWriter, QgsMarkerSymbol,
    QgsFillSymbol, QgsLineSymbol, QgsWkbTypes, Qgis)
from qgis.gui import QgsMapTool, QgsRubberBand
from .core import Client, Cancelled, osm_download, address_links, collect, FOOD, SHOPS

CREDIT = 'made for personal and public use, 2024 · published 2026'

class AreaTool(QgsMapTool):
    def __init__(self, canvas, done):
        super().__init__(canvas)
        self.canvas, self.done, self.start = canvas, done, None
        self.band = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
        self.band.setColor(QColor(35,154,129,90));self.band.setWidth(2)
    def canvasPressEvent(self, event):
        self.start = event.mapPoint()
    def canvasMoveEvent(self, event):
        if self.start is not None:
            from qgis.core import QgsRectangle
            self.band.setToGeometry(QgsGeometry.fromRect(QgsRectangle(self.start,event.mapPoint())),None)
    def canvasReleaseEvent(self,event):
        if self.start is None:return
        from qgis.core import QgsRectangle
        rectangle=QgsRectangle(self.start,event.mapPoint());self.start=None
        if rectangle.width()>0 and rectangle.height()>0:
            self.done(QgsGeometry.fromRect(rectangle), self.canvas.mapSettings().destinationCrs())
    def deactivate(self):
        self.band.reset(QgsWkbTypes.PolygonGeometry)
        super().deactivate()

class ImportTask(QgsTask):
    message = pyqtSignal(str)
    def __init__(self, options, complete, status):
        super().__init__('Prawdom_in: импорт области', QgsTask.CanCancel)
        self.options, self.complete, self.status = options, complete, status
        self.result, self.error = None, None
    def report(self, message):
        self.message.emit(message)
    def run(self):
        try:
            client=Client(self.options['cache'],self.isCanceled)
            elements=[];messages=[]
            if self.options['auto']:
                elements=osm_download(client,self.options['bbox'],self.report)
                self.setProgress(20)
                urls,errors=address_links(client,elements,self.report);messages.extend(errors)
            else:urls=self.options['urls']
            client.check();self.setProgress(40)
            rows,errors=collect(client,urls,self.report);messages.extend(errors)
            client.check();self.result={'rows':rows,'elements':elements,'messages':messages}
            self.setProgress(100);return True
        except Cancelled:
            self.error='Импорт отменён.';return False
        except Exception as ex:
            self.error=str(ex);return False
    def finished(self, ok):
        self.complete(self, ok)

def geometry(record):
    """Native QGIS geometry; closed rings assembled from OSM relation members."""
    def polygon(points):
        if len(points)<4 or points[0]!=points[-1]:return None
        return QgsGeometry.fromPolygonXY([[QgsPointXY(*p) for p in points]])
    def stitch(parts):
        parts=[list(p) for p in parts if len(p)>1];rings=[]
        while parts:
            chain=parts.pop()
            while chain[0]!=chain[-1]:
                found=False
                for i,p in enumerate(parts):
                    if chain[-1]==p[0]:chain+=p[1:]
                    elif chain[-1]==p[-1]:chain+=list(reversed(p[:-1]))
                    elif chain[0]==p[-1]:chain=p[:-1]+chain
                    elif chain[0]==p[0]:chain=list(reversed(p[1:]))+chain
                    else:continue
                    parts.pop(i);found=True;break
                if not found:break
            g=polygon(chain)
            if g:rings.append(g)
        return QgsGeometry.unaryUnion(rings) if rings else None
    if record['type']=='node':return QgsGeometry.fromPointXY(QgsPointXY(*record['coords'][0]))
    if record['type']=='way':
        pts=record['coords']
        if len(pts)<2:return None
        tags=record['tags']
        area=(tags.get('building') not in (None,'no') or tags.get('landuse') or tags.get('leisure') or tags.get('natural') in ('water','wood','grassland','scrub') or tags.get('area')=='yes')
        if area and pts[0]==pts[-1]:return polygon(pts)
        return QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in pts])
    members=record.get('members',[])
    g=stitch([m['coords'] for m in members if m['role']!='inner'])
    inside=stitch([m['coords'] for m in members if m['role']=='inner'])
    if g and inside:g=g.difference(inside)
    return g

def memory(kind,name,fields):
    l=QgsVectorLayer(kind+'?crs=EPSG:32637',name,'memory')
    l.dataProvider().addAttributes([QgsField(n,t) for n,t in fields]);l.updateFields();return l

def feature(layer,geom,attributes):
    if not geom or geom.isEmpty():return
    if layer.geometryType()==QgsWkbTypes.PolygonGeometry:
        if geom.type()!=QgsWkbTypes.PolygonGeometry:return
        geom.convertToMultiType()
    if layer.geometryType()==QgsWkbTypes.LineGeometry:
        if geom.type()!=QgsWkbTypes.LineGeometry:return
        geom.convertToMultiType()
    f=QgsFeature(layer.fields());f.setGeometry(geom);f.setAttributes(attributes);layer.dataProvider().addFeatures([f])

def style(layer,kind):
    if kind=='population':symbol=QgsMarkerSymbol.createSimple({'name':'circle','size':'1.4','color':'#9B9B9B','outline_style':'no'})
    elif kind=='food':symbol=QgsMarkerSymbol.createSimple({'name':'circle','size':'2','color':'#F06449','outline_color':'white','outline_width':'.25'})
    elif kind=='shops':symbol=QgsMarkerSymbol.createSimple({'name':'diamond','size':'2.2','color':'#3155D9','outline_color':'white','outline_width':'.25'})
    elif kind=='roads':symbol=QgsLineSymbol.createSimple({'line_color':'#C9CDCC','line_width':'.17'})
    else:
        colors={'buildings':'#D4D6D5','greenery':'#EEF0EA','water':'#E2E8EA','area':'91,184,161,65'}
        symbol=QgsFillSymbol.createSimple({'color':colors[kind],'outline_style':'solid' if kind=='area' else 'no','outline_color':'#239A81','outline_width':'.22'})
    layer.renderer().setSymbol(symbol)

def save_result(result, options):
    """Runs on the GUI thread. Saves real GeoPackage layers, never temporary-only."""
    project=QgsProject.instance();crs=QgsCoordinateReferenceSystem('EPSG:32637')
    transform=QgsCoordinateTransform(QgsCoordinateReferenceSystem('EPSG:4326'),crs,project)
    area=QgsGeometry.fromWkt(options['area']) if options['area'] else None
    if area:area.transform(transform);area=area.makeValid()
    common=[('название',QVariant.String),('тип',QVariant.String),('OSM_ID',QVariant.String)]
    layers={
      'population':memory('Point','Население · Prawdom',[('адрес',QVariant.String),('жилая_площадь',QVariant.Double),('жители',QVariant.Int),('норма_м2',QVariant.Double),('источник',QVariant.String),('получено_UTC',QVariant.String)]),
      'food':memory('Point','Общепит',common), 'shops':memory('Point','Продуктовые магазины',common),
      'buildings':memory('MultiPolygon','Здания',common),'roads':memory('MultiLineString','Улицы',common),
      'greenery':memory('MultiPolygon','Озеленение',common),'water':memory('MultiPolygon','Водоёмы',common),
      'area':memory('MultiPolygon','Область исследования',[('название',QVariant.String)])}
    excluded=0
    for row in result['rows']:
        point=QgsGeometry.fromPointXY(QgsPointXY(row['lon'],row['lat']));point.transform(transform)
        if area and not point.intersects(area):excluded+=1;continue
        feature(layers['population'],point,[row['address'],row['area'],int(math.floor(row['area']/options['norm']+.5)),options['norm'],row['source'],row['fetched_at']])
    members={m['ref'] for e in result['elements'] if e['type']=='relation' and e['tags'].get('building') for m in e.get('members',[])}
    bad=0
    for record in result['elements']:
        t=record['tags'];kinds=[]
        if t.get('building') not in (None,'no') and not(record['type']=='way' and record['id'] in members):kinds.append('buildings')
        if t.get('highway') and record['type']=='way' and t.get('area')!='yes':kinds.append('roads')
        if t.get('natural')=='water' or t.get('waterway')=='riverbank':kinds.append('water')
        if t.get('landuse') in ('grass','forest','meadow','village_green') or t.get('leisure') in ('park','garden') or t.get('natural') in ('wood','grassland','scrub'):kinds.append('greenery')
        if t.get('amenity') in FOOD:kinds.append('food')
        if t.get('shop') in SHOPS:kinds.append('shops')
        if not kinds:continue
        try:
            geom=geometry(record)
            if not geom or geom.isEmpty():continue
            geom.transform(transform)
            if not geom.isGeosValid():geom=geom.makeValid()
            for kind in kinds:
                g=QgsGeometry(geom)
                if kind in ('food','shops'):
                    if g.type()!=QgsWkbTypes.PointGeometry:g=g.pointOnSurface()
                    if area and not g.intersects(area):continue
                elif area:g=g.intersection(area)
                feature(layers[kind],g,[t.get('name',''),t.get('building',t.get('highway',t.get('amenity',t.get('shop','')))),record['type']+str(record['id'])])
        except Exception:bad+=1
    if area:feature(layers['area'],area,['Выбранная область; не расчётная изохрона'])
    pop=list(layers['population'].getFeatures());counts={'дома':len(pop),'жилая_площадь':sum(f['жилая_площадь'] for f in pop),'жители':sum(f['жители'] for f in pop),'общепит':layers['food'].featureCount(),'магазины':layers['shops'].featureCount(),'за_границей':excluded,'ошибки_карточек':len(result['messages']),'ошибки_геометрий':bad}
    if not any(l.featureCount() for l in layers.values()):raise ValueError('Нет объектов для сохранения. Проверьте журнал и выбранную область.')
    output=Path(options['output']);output.mkdir(parents=True,exist_ok=True)
    stem='Prawdom_in_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f');path=output/(stem+'.gpkg')
    saved={};first=True
    for key,l in layers.items():
        if not l.featureCount():continue
        l.updateExtents();style(l,key)
        meta=l.metadata();meta.setTitle(l.name());meta.setAbstract('Prawdom_in 1.4.4.7. '+('Жилая площадь из публичных карточек prawdom.ru; жители = round(жилая_площадь / норма), не перепись. Ссылки и дата получения сохранены для каждого дома.' if key=='population' else '© OpenStreetMap contributors, ODbL. Область исследования задаётся пользователем и не является автоматически рассчитанной изохроной.'));l.setMetadata(meta)
        opt=QgsVectorFileWriter.SaveVectorOptions();opt.driverName='GPKG';opt.layerName=key;opt.actionOnExistingFile=QgsVectorFileWriter.CreateOrOverwriteFile if first else QgsVectorFileWriter.CreateOrOverwriteLayer
        answer=QgsVectorFileWriter.writeAsVectorFormatV3(l,str(path),project.transformContext(),opt)
        if answer[0]!=QgsVectorFileWriter.NoError:raise ValueError('Ошибка записи GeoPackage: '+str(answer))
        first=False
        q=QgsVectorLayer(str(path)+'|layername='+key,l.name(),'ogr')
        if not q.isValid():raise ValueError('Не удалось открыть сохранённый слой '+l.name())
        style(q,key);q.setMetadata(meta);q.saveStyleToDatabase('Prawdom_in','Цвета проекта',True,'');saved[key]=q
    group=project.layerTreeRoot().insertGroup(0,'Prawdom_in · '+datetime.now().strftime('%H:%M:%S'))
    for key in ['population','food','shops','area','buildings','roads','greenery','water']:
        if key in saved:
            q=saved[key];project.addMapLayer(q,False);node=group.addLayer(q)
            if key=='population' and result['elements'] and not options['show_population']:node.setItemVisibilityChecked(False)
    order=[saved[k] for k in ['population','food','shops','area','buildings','roads','greenery','water'] if k in saved]
    tree=project.layerTreeRoot();prior=tree.customLayerOrder() if tree.hasCustomLayerOrder() else tree.layerOrder()
    tree.setHasCustomLayerOrder(True);tree.setCustomLayerOrder(order+[l for l in prior if l not in order])
    report={'plugin':'Prawdom_in 1.4.4.7','created_at':datetime.now().astimezone().isoformat(),'norm_m2':options['norm'],'summary':counts,'messages':result['messages'],'gpkg':path.name,'method':'Жители = округлённая жилая площадь / норма по каждому дому. Подсчёт внутри выбранной области по координатам. Площадь не прошедших сопоставление домов не восстанавливается. Категории POI как в исходном задании; supermarket не включён.'}
    log=output/(stem+'_журнал.json');log.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return saved,counts,str(path),str(log)

class ImportDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow());self.iface=iface;self.task=None;self.area=None;self.tool=None;self.old_tool=None
        self.setWindowTitle('Prawdom_in');self.resize(600,530)
        box=QVBoxLayout(self)
        title=QLabel('Prawdom_in');title.setFont(QFont('Arial',20,QFont.Bold));box.addWidget(title)
        box.addWidget(QLabel('Москва · импорт по выбранной области'))
        area_box=QGroupBox('1. Участок');row=QVBoxLayout(area_box);buttons=QHBoxLayout()
        for name,fn in [('Обвести рамкой',self.draw_area),('Выделенный полигон',self.selected_area),('Область окна',self.canvas_area)]:
            b=QPushButton(name);b.setAutoDefault(False);b.clicked.connect(fn);buttons.addWidget(b)
        row.addLayout(buttons);self.area_label=QLabel('Выберите область на карте.');row.addWidget(self.area_label);box.addWidget(area_box)
        self.mode=QCheckBox('Импорт по ссылкам вместо автоматического поиска адресов');box.addWidget(self.mode)
        self.urls=QPlainTextEdit();self.urls.setPlaceholderText('Ссылки на prawdom.ru, по одной на строку');self.urls.setMaximumHeight(90);self.urls.hide();box.addWidget(self.urls)
        self.mode.toggled.connect(self.urls.setVisible)
        settings=QHBoxLayout();settings.addWidget(QLabel('Жилая площадь на человека:'));self.norm=QDoubleSpinBox();self.norm.setRange(1,100);self.norm.setValue(18);self.norm.setSuffix(' м²');settings.addWidget(self.norm);settings.addStretch();box.addLayout(settings)
        self.show_population=QCheckBox('Показывать точки домов поверх карты');box.addWidget(self.show_population)
        pathrow=QHBoxLayout();base=QgsProject.instance().homePath() or QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        self.output=QLineEdit(str(Path(base)/'Prawdom_in_данные'));pathrow.addWidget(self.output);pick=QPushButton('Папка…');pick.clicked.connect(self.choose_output);pathrow.addWidget(pick);box.addLayout(pathrow)
        self.status=QLabel('Готов к импорту');self.status.setWordWrap(True);box.addWidget(self.status)
        self.progress=QProgressBar();self.progress.setRange(0,100);box.addWidget(self.progress)
        bottom=QHBoxLayout();self.start_button=QPushButton('Импортировать');self.start_button.clicked.connect(self.start);bottom.addWidget(self.start_button);self.stop_button=QPushButton('Отмена');self.stop_button.clicked.connect(self.cancel);self.stop_button.setEnabled(False);bottom.addWidget(self.stop_button);about=QPushButton('О плагине');about.clicked.connect(self.about);bottom.addWidget(about);box.addLayout(bottom)
    def about(self):
        QMessageBox.information(self,'О Prawdom_in','Prawdom_in · версия 1.4.4.7\n\nИмпорт жилой площади, координат и источников Prawdom.\nНаселение — оценка по норме 18 м², а не перепись.\n\nПользовательская подпись:\n'+CREDIT+'\n\nЛокальная сборка: 2026. ')
    def choose_output(self):
        path=QFileDialog.getExistingDirectory(self,'Папка результатов',self.output.text())
        if path:self.output.setText(path)
    def set_area(self,g,crs):
        try:
            g=QgsGeometry(g);g.transform(QgsCoordinateTransform(crs,QgsCoordinateReferenceSystem('EPSG:4326'),QgsProject.instance()));g=g.makeValid()
            if g.isEmpty() or g.type()!=QgsWkbTypes.PolygonGeometry:raise ValueError('Выберите полигональную область.')
            b=g.boundingBox();width=b.width()*111.32*math.cos(math.radians(b.center().y()));height=b.height()*111.32
            if width*height>25:raise ValueError('Выберите участок до 25 км², чтобы не перегружать источники.')
            self.area=g;self.area_label.setText(f'Область готова · примерно {width:.2f} × {height:.2f} км')
        except Exception as ex:QMessageBox.warning(self,'Область',str(ex))
        if self.tool:
            self.iface.mapCanvas().unsetMapTool(self.tool)
            if self.old_tool:self.iface.mapCanvas().setMapTool(self.old_tool)
            self.tool=None
        self.show();self.raise_()
    def draw_area(self):
        self.old_tool=self.iface.mapCanvas().mapTool();self.tool=AreaTool(self.iface.mapCanvas(),self.set_area);self.hide();self.iface.mapCanvas().setMapTool(self.tool)
    def canvas_area(self):self.set_area(QgsGeometry.fromRect(self.iface.mapCanvas().extent()),self.iface.mapCanvas().mapSettings().destinationCrs())
    def selected_area(self):
        l=self.iface.activeLayer()
        if not isinstance(l,QgsVectorLayer) or l.geometryType()!=QgsWkbTypes.PolygonGeometry or not l.selectedFeatureCount():
            QMessageBox.information(self,'Выделенный полигон','Выделите один или несколько полигонов в активном слое.');return
        self.set_area(QgsGeometry.unaryUnion([f.geometry() for f in l.getSelectedFeatures()]),l.crs())
    def start(self):
        if self.task:return
        auto=not self.mode.isChecked()
        if auto and not self.area:QMessageBox.information(self,'Участок','Сначала выберите область.');return
        if auto:
            b=self.area.boundingBox()
            if b.xMinimum()<36.8 or b.xMaximum()>38.2 or b.yMinimum()<55.3 or b.yMaximum()>56.2:
                QMessageBox.information(self,'Территория','Автоматический подбор адресов этой версии работает по Москве. Для других территорий доступны прямые ссылки.');return
        urls=self.urls.toPlainText().split()
        if not auto and not urls:QMessageBox.information(self,'Ссылки','Вставьте хотя бы одну ссылку на карточку.');return
        output=self.output.text().strip()
        if not output:QMessageBox.information(self,'Папка','Выберите папку результатов.');return
        opts={'auto':auto,'bbox':([b.xMinimum(),b.yMinimum(),b.xMaximum(),b.yMaximum()] if auto else None),'area':self.area.asWkt() if self.area else None,'urls':urls,'norm':self.norm.value(),'show_population':self.show_population.isChecked(),'output':output,'cache':str(Path(output)/'.prawdom_cache')}
        self.start_button.setEnabled(False);self.stop_button.setEnabled(True);self.mode.setEnabled(False);self.status.setText('Загрузка. Первый импорт может занять несколько минут; повторный использует кэш.')
        task=ImportTask(opts,self.complete,self.status);self.task=task;task.progressChanged.connect(lambda p:self.progress.setValue(int(p)));task.message.connect(self.status.setText);QgsApplication.taskManager().addTask(task)
    def complete(self,task,ok):
        self.task=None;self.start_button.setEnabled(True);self.stop_button.setEnabled(False);self.mode.setEnabled(True)
        if not ok or task.isCanceled():self.status.setText(task.error or 'Импорт отменён.');return
        try:
            layers,counts,path,log=save_result(task.result,task.options)
            focus=layers.get('area') or layers.get('population')
            if focus:
                extent=QgsRectangle(focus.extent())
                if extent.width()<1 or extent.height()<1:extent.grow(150)
                extent=QgsCoordinateTransform(focus.crs(),self.iface.mapCanvas().mapSettings().destinationCrs(),QgsProject.instance()).transformBoundingBox(extent)
                extent.scale(1.12);self.iface.mapCanvas().setExtent(extent)
            self.iface.mapCanvas().refresh()
            self.status.setText(f"Готово. Домов: {counts['дома']}; жителей ≈ {counts['жители']}; общепит: {counts['общепит']}; магазины: {counts['магазины']}.\nСохранено: {path}\nЖурнал: {log}")
            if not counts['дома']:QMessageBox.information(self,'Импорт завершён','Контекст сохранён, но жилые площади не найдены. Проверьте журнал. Можно импортировать дома по прямым ссылкам.')
        except Exception as ex:
            self.status.setText('Ошибка: '+str(ex));QMessageBox.warning(self,'Prawdom_in',str(ex))
    def cancel(self):
        if self.task:self.task.cancel();self.status.setText('Отмена после завершения текущего запроса…')
    def reject(self):
        if self.task:self.cancel();return
        super().reject()
    def closeEvent(self,event):
        if self.task:self.cancel();event.ignore()
        else:super().closeEvent(event)

class PrawdomPlugin:
    def __init__(self,iface):self.iface=iface;self.action=None;self.dialog=None
    def initGui(self):
        self.action=QAction('Prawdom_in · Импортировать область',self.iface.mainWindow());self.action.triggered.connect(self.run);self.iface.addPluginToMenu('Prawdom_in',self.action)
    def run(self):
        if self.dialog is None:self.dialog=ImportDialog(self.iface)
        self.dialog.show();self.dialog.raise_();self.dialog.activateWindow()
    def unload(self):
        if self.dialog:
            if self.dialog.task:self.dialog.task.cancel()
            self.dialog.hide()
        if self.action:self.iface.removePluginMenu('Prawdom_in',self.action);self.action.deleteLater()
