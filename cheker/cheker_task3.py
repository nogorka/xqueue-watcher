from skimage.metrics import structural_similarity as compare_ssim
import cv2

def check_render():
    imageA = cv2.imread("cheker/001.png")
    imageB = cv2.imread("путь к изображению из отклика")

    grayA = cv2.cvtColor(imageA, cv2.COLOR_BGR2GRAY)
    grayB = cv2.cvtColor(imageB, cv2.COLOR_BGR2GRAY)

    return compare_ssim(grayA, grayB, full=True)[0] * 100

"""
где score - регулируемая обсуждаемая величина. 
Для следующего пака изображений проверка выше проходит только для файла 002.png, 
исходное изображение - 001.png, остальные два изображения проверку по схожести не проходят. 
При величине score > 94 проверку проходят все 3 изображения.

"""
