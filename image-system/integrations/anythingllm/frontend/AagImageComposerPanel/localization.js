import HE_TAXONOMY_LABELS from "./heTaxonomyLabels";

export const DEFAULT_UI_LANGUAGE = "en";
export const UI_LANGUAGE_KEY = "aag.image-composer.v1.2.ui-language";
export const UI_LANGUAGES = Object.freeze([
  ["en", "English"],
  ["he", "עברית"],
]);

const HE_UI = Object.freeze({
  composerControls: "מצב ופקדים של AAG Image Composer",
  modeGroup: "מצב Image Composer",
  autoMode: "אוטומטי",
  advancedMode: "מתקדם",
  language: "שפה",
  hideControls: "הסתר פקדים",
  showControls: "הצג פקדים",
  intro:
    "הגדירו כאן אילוצי הפקה, והשאירו את הבקשה בשפה טבעית בתיבת ההודעה שלמטה.",
  whatToCreate: "מה ליצור",
  operation: "פעולה",
  editMode: "מצב עריכה",
  editModeHelp:
    "מצב שימור משאיר את מראה המקור ואת כל המאפיינים שלא ביקשתם לשנות במפורש בהודעת הצ׳אט הרגילה. בחרו בשינוי סגנון רק כשברצונכם לשנות את הסגנון החזותי.",
  referencePurpose: "מטרת תמונת הייחוס",
  referencePurposeHelp:
    "שימור זהות אדם משתמש במסלול הזהות הצילומי המאומת לאדם אחד ונכשל באופן סגור אם הפנים חסרות, מרובות, מוסתרות או אינן ניתנות להערכה. ייחוס חזותי כללי אינו מבטיח שימור זהות אדם.",
  identityRealisticCapability:
    "שמירת זהות משתמשת כרגע בעיבוד ריאליסטי שעבר אימות.",
  identityStyleConflict:
    "שמירת זהות האדם תומכת כרגע בעיבוד ריאליסטי שעבר אימות. לסגנונות חופשיים יותר בחרו 'שמירת ייחוס חזותי כללי'.",
  intendedUse: "שימוש מיועד",
  modelGuidance: "הנחיה למודל",
  seriesRelationship: "הקשר בין תמונות הסדרה",
  styleAppearance: "סגנון ומראה",
  styleNote:
    "הסגנון מנחה את הפרומפט שהמודל מחבר. כל התוצרים נשארים בפורמט רסטר, גם בבחירות דמויות וקטור, לוגו, מפה, אינפוגרפיקה או דוגמה.",
  visualFamily: "משפחת סגנון",
  visualSubfamily: "תת־משפחת סגנון",
  familyCountSuffix: "משפחות זמינות",
  familyLoadingHelp: "משפחות הסגנון נטענות…",
  chooseFamily: "יש לבחור תחילה משפחת סגנון",
  loadingFamilies: "טוען משפחות סגנון…",
  subfamilyCountSuffix: "תתי־משפחה זמינים",
  background: "רקע",
  backgroundHelp: "מנוע התמונות הנוכחי אינו מבטיח פיקסלים שקופים.",
  visibleText: "טקסט גלוי",
  visibleTextHelp:
    "שולט בטקסט שאמור להופיע בתוך התמונה. מנוע התמונות הנוכחי אינו מבטיח איות מדויק בטקסט המרונדר.",
  recentStyles: "סגנונות אחרונים",
  browseAtlas: "עיון באטלס החזותי",
  atlasBrowseHelp:
    "עיינו חזותית באטלס שהושלם; בחירת כרטיס תחיל את הסגנון על בקשת ה־Composer הנוכחית.",
  selectedVisualStyle: "הסגנון החזותי שנבחר",
  stylePreview: "תצוגה מקדימה של סגנון",
  openLargePreview: "פתיחת תצוגת סגנון גדולה",
  changeStyle: "שינוי",
  clearStyle: "ניקוי סגנון",
  visualAtlas: "האטלס החזותי",
  atlasCount: "493 סגנונות חזותיים שהושלמו — בחרו אחד כדי להחיל אותו",
  closeAtlas: "סגירת האטלס החזותי",
  searchStyles: "חיפוש סגנונות",
  searchStylesPlaceholder: "חיפוש סגנונות…",
  filterFamily: "סינון לפי משפחה",
  thumbnailSize: "גודל תמונות ממוזערות",
  thumbnailSizesmall: "קטן",
  thumbnailSizemedium: "בינוני",
  thumbnailSizelarge: "גדול",
  inspectStyle: "בדיקת",
  selectStyle: "בחירת סגנון",
  selectThisStyle: "בחירת סגנון זה",
  styleSelected: "הסגנון נבחר",
  closePreview: "סגירת התצוגה המקדימה",
  category: "קטגוריה",
  subcategory: "תת־קטגוריה",
  allFamilies: "כל המשפחות",
  stylesFound: "סגנונות נמצאו",
  noStylesFound: "לא נמצאו סגנונות המתאימים לחיפוש ולסינון המשפחה.",
  loadMoreStyles: "טעינת 48 סגנונות נוספים",
  largeStylePreview: "תצוגה גדולה של האטלס החזותי",
  sizeQuantity: "גודל וכמות",
  aspectRatio: "יחס גובה־רוחב",
  aspectRatioHelp:
    "היחסים 3:4, 2:3 ו־21:9 ומידות מותאמות אינם זמינים משום שהמנוע הפעיל אינו מכבד אותם במדויק.",
  imageCount: "מספר תמונות",
  customCount: "כמות מותאמת",
  sourceChanges: "מקור ושינויים",
  sourceImage: "תמונת מקור",
  sourcePreviousHelp: "ייעשה שימוש בתוצר התמונה האחרון שנוצר בשרשור זה.",
  sourcePreviousUnavailableHelp:
    "אין כרגע תמונה שנוצרה בשרשור זה. בחרו בתמונה שהועלתה במקום זאת.",
  sourceUploadHelp:
    "העלו תמונה אחת או יותר יחד עם הודעת הצ׳אט הרגילה הזאת, ולאחר מכן בחרו באיזו להשתמש.",
  selectedSource: "נבחרה:",
  selectedSourcePreview: "תצוגה מקדימה של תמונת המקור שנבחרה",
  sourcePreviewUnavailable:
    "התצוגה המקדימה אינה זמינה; המקור המפוקח עדיין יאומת מחדש בזמן השליחה.",
  noImageForEdit:
    "אין תמונה שנוצרה בשרשור זה. בחרו בתמונה שהועלתה וצרפו מקור תקין.",
  noImageForReference:
    "אין תמונה שנוצרה בשרשור זה. בחרו בתמונה שהועלתה וצרפו תמונת ייחוס תקינה.",
  noImageForUpscale:
    "אין תמונה שנוצרה בשרשור זה. בחרו בתמונה שהועלתה וצרפו מקור תקין.",
  sourceUploadRequired: "יש לבחור בין תמונת מקור אחת לשמונה תמונות מקור שהועלו.",
  referenceArtifactUnavailable:
    "לא ניתן לקרוא את תוצר השרשור שנבחר כתמונת ייחוס. בחרו בתמונה שהועלתה או נסו שוב.",
  referenceArtifactChanged:
    "תוצר השרשור שנבחר נכשל בבדיקת התקינות ולא צורף.",
  identityReferenceCount:
    "ייחוס לזהות אדם דורש תמונת מקור אחת בדיוק ובה אדם אחד שפניו נראות בבירור.",
  sourceImages: "תמונות מקור",
  sourceImagesHelp: "עד 8 קובצי PNG, JPEG או WebP; עד 15MB לקובץ ועד 22MB בקידוד כולל.",
  useUpload: "שימוש בהעלאה זו",
  useUploadHelp: "רק ההעלאה הממוספרת שנבחרה תשמש כמקור להפקה.",
  upload: "העלאה",
  preservation: "שימור",
  preservationHelp: "שימור זהות אדם דורש תמונת מקור נוכחית אחת בדיוק ובה אדם.",
  qualityProcessing: "איכות ועיבוד",
  technicalQuality: "איכות טכנית",
  technicalQualityHelp: "הגדרה זו שולטת בנתיב הטכני, לא בפרטים אמנותיים או ביופי.",
  identityTechnicalQualityHelp:
    "מתכון הזהות המאומת והנעול נשאר ללא שינוי.",
  identityGeneration: "יצירת זהות",
  validatedIdentityQuality: "איכות זהות מאומתת",
  finalOutputQuality: "איכות הפלט הסופי",
  finalOutputQualityHelp:
    "שיפור 2× מופעל רק לאחר שהתמונה המפוקחת נוצרה ואומתה.",
  upscaleFactor: "מקדם הגדלה",
  seed: "Seed",
  seedHelp: "Seed מדויק אופציונלי לתמונה אחת שנוצרת. השאירו ריק לבחירה אוטומטית.",
  specialRequirements: "דרישות מיוחדות",
  modelDirectsTitle: "המודל עדיין קובע:",
  modelDirectsBody:
    "קומפוזיציה, מצלמה ומסגור מעבר ליחס שנבחר, תאורה, חומרים, מרקמים, אווירה, עומק, היררכיה חזותית וניסוח מקצועי של הפרומפט.",
  limitsTitle: "מגבלות מנוע התמונות הנוכחי:",
  limitsBody:
    "אין יצוא SVG/וקטור אמיתי; פיקסלים שקופים, טקסט מרונדר מדויק, ריצוף חלק מאומת, דיוק GIS/גאוגרפי וגאומטריה דטרמיניסטית באינפוגרפיקה או בדיאגרמות אינם מובטחים.",
  selectionsReady: "בחירות ה־Composer מוכנות",
  nextMessageHelp: "הגדרות אלה יחולו על הודעת הצ׳אט הרגילה הבאה שלכם.",
  sendHelp: "השתמשו ב־Enter או בכפתור השליחה הרגיל; אין שליחה נפרדת של ה־Composer.",
  attachingSelections: "מצרף את בחירות ה־Composer",
  validatingNormalMessage: "מאמת את הפקדים עבור הודעת הצ׳אט הרגילה…",
  selectionsAttached: "בחירות ה־Composer צורפו",
  sendingNormalMessage: "ההודעה נשלחת בשיחת AnythingLLM הרגילה.",
  messageNotSent: "ההודעה לא נשלחה",
  noPreviousArtifact: "התמונה האחרונה שנוצרה — אינה זמינה",
  previousArtifact: "התמונה האחרונה שנוצרה בשרשור זה",
});

const HE_OPTIONS = Object.freeze({
  "operation.create": "יצירת תמונה אחת",
  "operation.batch": "אצווה / סדרה",
  "operation.reference": "יצירה מתמונת ייחוס",
  "operation.transform": "עריכה / שינוי",
  "operation.upscale": "הגדלה / שיפור",
  "editMode.preserve": "שימור המראה הנוכחי",
  "editMode.restyle": "שינוי סגנון התמונה",
  "referencePurpose.identity": "שימור זהות האדם",
  "referencePurpose.general_visual": "שימור ייחוס חזותי כללי",
  "purpose.auto": "אוטומטי",
  "purpose.general": "כללי",
  "purpose.wallpaper": "טפט",
  "purpose.social": "גרפיקה לרשתות חברתיות",
  "purpose.poster": "כרזה",
  "purpose.product_commercial": "מוצר / מסחרי",
  "purpose.presentation": "מצגת",
  "purpose.print": "הדפסה",
  "purpose.thumbnail": "תמונה ממוזערת",
  "purpose.banner": "באנר",
  "relationship.independent": "תמונות עצמאיות",
  "relationship.same_concept_different_compositions": "אותו רעיון, קומפוזיציות שונות",
  "relationship.coordinated_series": "סדרה מתואמת",
  "relationship.variations": "וריאציות",
  "background.auto": "אוטומטי",
  "background.preserve_source": "שימור רקע המקור",
  "background.solid_plain": "אחיד / פשוט",
  "background.scene_background": "רקע סצנה",
  "background.isolated_no_background": "מראה מבודד / ללא רקע גלוי",
  "visibleText.auto": "אוטומטי",
  "visibleText.none": "ללא טקסט גלוי",
  "visibleText.model_decides": "המודל מחליט",
  "ratio.auto": "אוטומטי",
  "ratio.1:1": "1:1 ריבוע",
  "ratio.4:3": "4:3 לרוחב קלאסי",
  "ratio.3:2": "3:2 צילום לרוחב",
  "ratio.16:9": "16:9 מסך רחב",
  "ratio.9:16": "9:16 אנכי / טלפון",
  "ratio.landscape": "רוחב אוטומטי",
  "ratio.portrait": "אורך אוטומטי",
  "count.custom": "מותאם 2–10",
  "quality.auto": "אוטומטי — התנהגות V5.2 המאושרת",
  "quality.fast": "מהיר",
  "quality.balanced": "מאוזן",
  "quality.quality": "איכות טכנית מרבית",
  "identityQuality.auto": "איכות זהות מאומתת",
  "finalOutputQuality.standard": "רגילה",
  "finalOutputQuality.enhanced_2x": "משופרת 2×",
  "scale.auto": "אוטומטי — ברירת המחדל של השרת",
  "source.current_attachment": "תמונה שהועלתה",
  "source.previous_artifact": "התמונה האחרונה שנוצרה בשרשור זה",
  "preservation.subject": "שימור הנושא / התוכן",
  "preservation.identity": "שימור אותו אדם מזוהה",
  "taxonomy.auto": "אוטומטי",
});

export function loadUiLanguage() {
  try {
    return localStorage.getItem(UI_LANGUAGE_KEY) === "he" ? "he" : DEFAULT_UI_LANGUAGE;
  } catch {
    return DEFAULT_UI_LANGUAGE;
  }
}

export function setStoredUiLanguage(language) {
  try {
    localStorage.setItem(UI_LANGUAGE_KEY, language === "he" ? "he" : DEFAULT_UI_LANGUAGE);
  } catch {
    // A blocked localStorage must not affect Composer operation.
  }
}

export function uiText(language, key, english) {
  return language === "he" ? HE_UI[key] || english : english;
}

export function optionLabel(language, scope, value, english) {
  if (language !== "he") return english;
  return HE_OPTIONS[`${scope}.${value}`] || english;
}

export function taxonomyLabel(language, familyId, entry, isFamily = false) {
  if (language !== "he") return entry.label;
  const key = isFamily ? `family/${entry.id}` : `${familyId}/${entry.id}`;
  return HE_TAXONOMY_LABELS[key] || entry.label;
}
